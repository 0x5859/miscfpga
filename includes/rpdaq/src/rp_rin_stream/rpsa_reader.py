"""Reader for the binary file format produced by Red Pitaya's official
``rpsa_client`` streaming tool (the ARM C++ client used on the board).

Why this exists
---------------
The original project wrote a Python ``ADCCallback`` that drained the
streaming-server through the SWIG binding. On the Zynq-7020's single ARM core
that path is bottlenecked at ~250 kSa/s/ch by per-element conversion of the
SWIG ``Int16Vector`` to numpy. The official ``rpsa_client`` C++ binary on the
same board sustains the full 3.9 MB/s with zero FPGA loss at decimation=128.

Project now uses ``rpsa_client`` directly; this module decodes its output.

File format (reverse-engineered, validated byte-for-byte against the
``convert_tool`` CSV reference on both single-pack and multi-pack captures):

Each network "pack" is serialised as one self-contained block::

    +--------------------------+   offset 0
    | header  (112 bytes)      |
    | ...                      |
    | uint32 data_bytes  @0x68 |   little-endian, total bytes of the data area
    | uint32 timestamp?  @0x6C |   monotonically increasing, micro/nanoseconds
    +--------------------------+   offset 112
    | CH1 raw int16 LE         |   data_bytes / num_active_channels  bytes
    | ...                      |
    +--------------------------+
    | CH2 raw int16 LE         |   (only if CH2 is active)
    | ...                      |
    +--------------------------+
    | footer  (12 bytes 0xFF)  |
    +--------------------------+

A multi-pack file is just the concatenation of these blocks.

When ``-l N`` is passed to ``rpsa_client`` the file contains exactly N samples
per channel: full packs of 131,072 samples each plus a (possibly truncated)
final pack carrying ``N mod 131072`` samples. The final pack uses the same
header/footer wrapping; ``data_bytes`` reflects the truncated size.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

PACK_HEADER_SIZE = 112
PACK_FOOTER_SIZE = 12
DATA_BYTES_OFFSET = 0x68
EXPECTED_FOOTER = b"\xff" * PACK_FOOTER_SIZE


class RpsaLogError(RuntimeError):
    """Raised when rpsa_client's expected log files are missing or unparseable.

    The launcher treats this as a loss event: without the logs we cannot
    prove the capture was lossless, and silently calling that "no loss"
    would violate the project's continuous-capture guarantee.
    """


@dataclass
class RpsaPackRecord:
    """One ``Channel N: Pos P Get: G (K)`` record from .log.lost.txt."""

    channel: int
    pos: int       # cumulative source-time sample index this pack starts at
    got: int       # samples this pack delivered
    lost: int      # samples lost between previous pack and this pack (FPGA side)


@dataclass
class RpsaLossReport:
    """Loss summary parsed from rpsa_client's .log.txt and .log.lost.txt."""

    fpga_lost_per_channel: dict[int, int]
    file_buffer_lost: int
    memory_lost: int
    # Backwards-compatible flat list of per-pack `(K)` lost values across
    # ALL channels (CH1 then CH2 entries are interleaved as in the log).
    per_pack_lost: list[int]
    # New: per-channel pack records (Pos / Get / lost).
    pack_records_per_channel: dict[int, list[RpsaPackRecord]]
    # New: per-channel Pos-sequence contiguity verdict.
    # True iff Pos[i+1] == Pos[i] + Get[i] for every adjacent pair.
    pos_sequence_contiguous_per_channel: dict[int, bool]
    # New: per-channel count of Pos discontinuities (defensive metric).
    pos_sequence_gaps_per_channel: dict[int, int]
    total_samples_per_channel: dict[int, int]
    adc_speed_hz: float | None
    # The launcher relies on these flags to refuse to call a capture
    # "lossless" without proof. Both must be True for has_any_loss to be
    # trustworthy as False.
    log_txt_present: bool
    log_lost_txt_present: bool

    @property
    def logs_present(self) -> bool:
        return self.log_txt_present and self.log_lost_txt_present

    @property
    def per_pack_lost_records(self) -> int:
        """Number of `(K)` records seen across the log file. For a 2-channel
        capture this is 2 × pack_count because each line has CH1 and CH2."""

        return len(self.per_pack_lost)

    @property
    def pack_count_per_channel(self) -> dict[int, int]:
        return {ch: len(recs) for ch, recs in self.pack_records_per_channel.items()}

    @property
    def has_any_loss(self) -> bool:
        # Missing logs are themselves a loss signal; we cannot prove zero
        # loss without them.
        if not self.logs_present:
            return True
        if any(v > 0 for v in self.fpga_lost_per_channel.values()):
            return True
        if self.file_buffer_lost > 0 or self.memory_lost > 0:
            return True
        if any(v > 0 for v in self.per_pack_lost):
            return True
        # Defensive: if Pos sequence is non-contiguous on any channel,
        # treat as loss even if all (K) reported zero.  Strictly redundant
        # given the rpsa_client formula `Pos += samples + lost`, but it
        # protects us from a future upstream bug where (K) is mis-reported.
        if any(not v for v in self.pos_sequence_contiguous_per_channel.values()):
            return True
        return False


def parse_rpsa_logs(
    bin_path: str | Path,
    *,
    require_logs: bool = True,
) -> RpsaLossReport:
    """Parse the .log.txt + .log.lost.txt files that rpsa_client writes
    alongside the .bin and return a normalized loss report.

    By default this raises :class:`RpsaLogError` if either log file is
    missing or fundamentally unparseable (no ADC speed line, no per-channel
    block). Pass ``require_logs=False`` only for diagnostic / synthetic-test
    code paths — never for the real capture pipeline, because a missing log
    means we cannot prove the capture was lossless.
    """

    bin_path = Path(bin_path)
    log_txt = bin_path.with_suffix(bin_path.suffix + ".log.txt")
    log_lost = bin_path.with_suffix(bin_path.suffix + ".log.lost.txt")

    log_txt_present = log_txt.exists()
    log_lost_present = log_lost.exists()

    if require_logs and not (log_txt_present and log_lost_present):
        missing = []
        if not log_txt_present:
            missing.append(str(log_txt))
        if not log_lost_present:
            missing.append(str(log_lost))
        raise RpsaLogError(
            f"rpsa_client log file(s) missing — cannot prove lossless capture: {missing}"
        )

    fpga_lost: dict[int, int] = {}
    samples_per_channel: dict[int, int] = {}
    file_buffer_lost = 0
    memory_lost = 0
    adc_speed_hz: float | None = None

    if log_txt_present:
        text = log_txt.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Current ADC speed:\s+(\d+)", text)
        if m:
            adc_speed_hz = float(m.group(1))
        m = re.search(r"Lost data due to file write buffer overflow:\s+(\d+)", text)
        if m:
            file_buffer_lost = int(m.group(1))
        m = re.search(r"Loss of data due to lack of memory:\s+(\d+)", text)
        if m:
            memory_lost = int(m.group(1))
        # Per-channel sections: "Channel N" then "<num> Samples" then "FPGA: <num>"
        for ch_match in re.finditer(
            r"on:\s*Channel\s+(\d+)\s*\n\s*-(\d+)\s+Samples", text
        ):
            samples_per_channel[int(ch_match.group(1))] = int(ch_match.group(2))
        for ch_match in re.finditer(
            r"on:\s*Channel\s+(\d+).*?FPGA:\s+(\d+)\s+Samples", text, re.DOTALL
        ):
            fpga_lost[int(ch_match.group(1))] = int(ch_match.group(2))

        if require_logs and (
            adc_speed_hz is None or not samples_per_channel or not fpga_lost
        ):
            raise RpsaLogError(
                f"{log_txt} is unparseable: ADC speed / per-channel block not found"
            )

    per_pack_lost: list[int] = []
    pack_records_per_channel: dict[int, list[RpsaPackRecord]] = {}
    if log_lost_present:
        for line in log_lost.read_text(encoding="utf-8", errors="replace").splitlines():
            for m in re.finditer(
                r"Channel\s+(\d+):\s*Pos\s+(\d+)\s+Get:\s+(\d+)\s+\((\d+)\)",
                line,
            ):
                ch = int(m.group(1))
                rec = RpsaPackRecord(
                    channel=ch,
                    pos=int(m.group(2)),
                    got=int(m.group(3)),
                    lost=int(m.group(4)),
                )
                pack_records_per_channel.setdefault(ch, []).append(rec)
                per_pack_lost.append(rec.lost)
        if require_logs and not per_pack_lost:
            # Note: an empty .log.lost.txt is suspect — even a single-pack
            # capture writes one "Pos 0 Get: N (0)" line. Treat as unparseable.
            raise RpsaLogError(
                f"{log_lost} parsed to zero per-pack records (expected at least one)"
            )

    # Per-channel Pos-sequence contiguity check: Pos[i+1] should equal
    # Pos[i] + Get[i].  When `(K)`==0 for all packs this is guaranteed
    # by rpsa_client's own logging formula, so a violation indicates an
    # upstream bug (or that a record line was dropped from the log).
    pos_contig: dict[int, bool] = {}
    pos_gaps: dict[int, int] = {}
    for ch, recs in pack_records_per_channel.items():
        gaps = 0
        for i in range(1, len(recs)):
            if recs[i].pos != recs[i - 1].pos + recs[i - 1].got:
                gaps += 1
        pos_contig[ch] = gaps == 0
        pos_gaps[ch] = gaps

    return RpsaLossReport(
        fpga_lost_per_channel=fpga_lost,
        file_buffer_lost=file_buffer_lost,
        memory_lost=memory_lost,
        per_pack_lost=per_pack_lost,
        pack_records_per_channel=pack_records_per_channel,
        pos_sequence_contiguous_per_channel=pos_contig,
        pos_sequence_gaps_per_channel=pos_gaps,
        total_samples_per_channel=samples_per_channel,
        adc_speed_hz=adc_speed_hz,
        log_txt_present=log_txt_present,
        log_lost_txt_present=log_lost_present,
    )


def _detect_active_channels(header: bytes) -> int:
    """Sniff the header's per-channel "active" markers at offsets 0x00..0x03.

    Empirically: header[0]=0x02 means CH1 active, header[1]=0x02 means CH2
    active, and the same pattern likely extends to CH3/CH4 for 4-channel
    boards. Used to refuse to silently mis-split a single-channel capture
    when the caller asked for 2 channels.
    """

    return sum(1 for i in range(4) if header[i] == 0x02)


def iter_packs(
    bin_path: str | Path,
    num_channels: int | None = None,
) -> Iterator[tuple[int, list[np.ndarray]]]:
    """Yield ``(pack_index, [ch1_samples, ch2_samples, ...])`` for each pack
    in the file. Channels are returned as views into a freshly read buffer
    (caller is free to keep references).

    If ``num_channels`` is ``None`` (the default), the channel count is read
    from the first pack's header byte 0x00..0x03 and validated to stay
    constant across all packs. Pass an explicit integer only to assert a
    specific channel count and fail-fast on any drift.
    """

    bin_path = Path(bin_path)
    raw = bin_path.read_bytes()
    pos = 0
    pack_index = 0
    detected_channels: int | None = None
    while pos < len(raw):
        if pos + PACK_HEADER_SIZE > len(raw):
            raise ValueError(
                f"{bin_path}: pack {pack_index} header truncated at byte {pos}"
            )
        header = raw[pos : pos + PACK_HEADER_SIZE]
        data_bytes = struct.unpack_from("<I", header, DATA_BYTES_OFFSET)[0]
        active = _detect_active_channels(header)
        if active == 0:
            raise ValueError(
                f"{bin_path}: pack {pack_index} reports zero active channels "
                f"(header[0..3]={list(header[:4])})"
            )
        if num_channels is None:
            if detected_channels is None:
                detected_channels = active
            elif active != detected_channels:
                raise ValueError(
                    f"{bin_path}: pack {pack_index} channel count {active} "
                    f"differs from first pack {detected_channels}"
                )
            ch_count = detected_channels
        else:
            if active != num_channels:
                raise ValueError(
                    f"{bin_path}: pack {pack_index} declares {active} active "
                    f"channels but caller asked for {num_channels}"
                )
            ch_count = num_channels
        if data_bytes <= 0 or data_bytes % (2 * ch_count) != 0:
            raise ValueError(
                f"{bin_path}: pack {pack_index} has invalid data_bytes={data_bytes} "
                f"(active_channels={ch_count})"
            )
        pos += PACK_HEADER_SIZE
        if pos + data_bytes + PACK_FOOTER_SIZE > len(raw):
            raise ValueError(
                f"{bin_path}: pack {pack_index} data overruns file "
                f"(need {data_bytes + PACK_FOOTER_SIZE} more bytes, have {len(raw) - pos})"
            )
        data = raw[pos : pos + data_bytes]
        pos += data_bytes
        footer = raw[pos : pos + PACK_FOOTER_SIZE]
        pos += PACK_FOOTER_SIZE
        if footer != EXPECTED_FOOTER:
            raise ValueError(
                f"{bin_path}: pack {pack_index} bad footer 0x{footer.hex()}"
            )
        samples_per_ch = data_bytes // (2 * ch_count)
        arr = np.frombuffer(data, dtype="<i2")
        chans = [
            arr[ch * samples_per_ch : (ch + 1) * samples_per_ch]
            for ch in range(ch_count)
        ]
        yield pack_index, chans
        pack_index += 1


def detect_num_channels(bin_path: str | Path) -> int:
    """Return the active-channel count declared in the first pack header."""

    bin_path = Path(bin_path)
    with bin_path.open("rb") as f:
        header = f.read(PACK_HEADER_SIZE)
    if len(header) < PACK_HEADER_SIZE:
        raise ValueError(f"{bin_path}: file shorter than one pack header")
    n = _detect_active_channels(header)
    if n == 0:
        raise ValueError(f"{bin_path}: header reports zero active channels")
    return n


def _detect_bit16_packing(arr: np.ndarray) -> bool:
    """Return True if ``arr`` looks like a BIT_16 ``(adc14 << 2) | 0x3`` stream.

    The streaming-server's ``BIT_16 raw`` mode left-shifts the 14-bit signed
    ADC value by 2 and ORs the lowest 2 bits with ``0b11``, producing int16
    values whose low 2 bits are uniformly ``0b11`` and whose values fall on
    a ``4``-grid. This pattern is statistically impossible for natural noise,
    so a single-pass check on the low 2 bits is decisive.

    Threshold = 0.90. Rationale: an unpacked 14-bit signed stream stored
    as int16 distributes its low 2 bits ~25 % each, so 0.90 rejects all
    realistic random-noise inputs by a wide margin. We use 0.90 (not 0.99)
    because rare boundary transitions / firmware glitches leave a small
    fraction (observed up to ~1.1 % on noisy 1811-FC channels) of samples
    with different low bits without invalidating the packing
    interpretation. Edge case to watch: a stuck-near-(-1) signal (where
    `-1 = 0xFFFF` already has low2bits = `0b11`) could falsely trigger;
    avoidable by checking acquisition metadata instead, but unlikely in
    practice for the dual-PD use case.
    """
    if arr.size == 0:
        return False
    sample = arr[: min(arr.size, 100_000)]
    return float(np.mean((sample & 3) == 3)) >= 0.90


def read_streams(
    bin_path: str | Path,
    num_channels: int | None = None,
    *,
    unpack_bit16: bool | str = "auto",
) -> list[np.ndarray]:
    """Read entire .bin and return one concatenated numpy array per channel.

    Channel count is auto-detected from the first pack header unless
    ``num_channels`` is set explicitly. For the typical 60-second @ 976
    kSa/s capture this is ~120 MB in RAM; prefer ``iter_packs`` for very
    long captures.

    BIT_16 unpacking
    ----------------
    The Red Pitaya streaming-server's ``BIT_16 raw`` mode packs the 14-bit
    signed ADC value as ``(adc14 << 2) | 0x3``. Returned int16 values are
    therefore 4x the true ADC counts plus a fixed-3 offset in the lowest
    2 bits, which silently inflates downstream voltage conversions by 4x.
    With ``unpack_bit16='auto'`` (default) this is detected per-channel
    on the first 100 k samples (low-2-bits == 0b11 in >=99 % of them) and
    the channel is right-arithmetic-shifted by 2. Pass ``True`` to force
    unpack regardless, or ``False`` to disable.
    """

    detected = detect_num_channels(bin_path) if num_channels is None else num_channels
    streams: list[list[np.ndarray]] = [[] for _ in range(detected)]
    for _, chans in iter_packs(bin_path, detected):
        for ch, arr in enumerate(chans):
            streams[ch].append(arr)
    out = [
        (np.concatenate(s) if s else np.empty(0, dtype=np.int16))
        for s in streams
    ]
    if unpack_bit16 is False:
        return out
    for ch in range(len(out)):
        if out[ch].size == 0:
            continue
        do_unpack = (unpack_bit16 is True) or _detect_bit16_packing(out[ch])
        if do_unpack:
            # Arithmetic right-shift by 2 to undo (adc14 << 2) | 0x3 packing.
            # numpy's `>>` on signed dtypes is arithmetic, so this preserves
            # sign for negative ADC values.
            out[ch] = (out[ch].astype(np.int16) >> 2).astype(np.int16)
    return out


def iter_interleaved_blocks(
    bin_path: str | Path,
    num_channels: int | None = None,
) -> Iterator[np.ndarray]:
    """Yield numpy arrays of shape ``(samples_in_pack, num_channels)``,
    int16, one per pack. Channel count is auto-detected from the first
    pack header unless set explicitly."""

    detected = detect_num_channels(bin_path) if num_channels is None else num_channels
    for _, chans in iter_packs(bin_path, detected):
        n = chans[0].shape[0]
        out = np.empty((n, detected), dtype="<i2")
        for ch, arr in enumerate(chans):
            out[:, ch] = arr
        yield out


def total_samples_per_channel(
    bin_path: str | Path,
    num_channels: int | None = None,
) -> int:
    """Walk all packs without keeping data, return total CH1 samples count.

    Channel count is auto-detected from the first pack header unless set.
    """

    detected = detect_num_channels(bin_path) if num_channels is None else num_channels
    n = 0
    for _, chans in iter_packs(bin_path, detected):
        n += chans[0].shape[0]
    return n


__all__ = [
    "PACK_HEADER_SIZE",
    "PACK_FOOTER_SIZE",
    "DATA_BYTES_OFFSET",
    "RpsaLogError",
    "RpsaLossReport",
    "parse_rpsa_logs",
    "iter_packs",
    "read_streams",
    "iter_interleaved_blocks",
    "total_samples_per_channel",
    "detect_num_channels",
]
