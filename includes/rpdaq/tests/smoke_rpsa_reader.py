"""Hardware-free smoke test for the rpsa_client BIN parser + converters.

Synthesises small rpsa_client-format files (matching the byte-level layout
documented in rpsa_reader.py), then runs:
  * Parser: dual-channel, single-channel auto-detect, mid-pack truncation
  * Loss-detection: hard-fail on missing/empty logs
  * Length cross-check in metadata.write_metadata: lossy when file shorter
    than -l limit and not interrupted
  * convert.convert_run_to_csv     → raw-counts CSV
  * bin_to_volts_csv.convert_run_to_volts_csv → time/voltage CSV
  * metadata.write_metadata        → config.json + summary.json

Run from repo root:
    PYTHONPATH=src python tests/smoke_rpsa_reader.py
"""
from __future__ import annotations

import csv
import json
import struct
import tempfile
from pathlib import Path

import numpy as np

from rp_rin_stream.rpsa_reader import (
    PACK_HEADER_SIZE,
    PACK_FOOTER_SIZE,
    DATA_BYTES_OFFSET,
    RpsaLogError,
    detect_num_channels,
    iter_interleaved_blocks,
    parse_rpsa_logs,
    read_streams,
    total_samples_per_channel,
)
from rp_rin_stream.convert import convert_run_to_csv
from rp_rin_stream.bin_to_volts_csv import convert_run_to_volts_csv
from rp_rin_stream.metadata import write_metadata


def synth_rpsa_pack(ch1: np.ndarray, ch2: np.ndarray | None = None) -> bytes:
    """Build one rpsa_client pack: 112-byte header + CH1 block [+ CH2 block]
    + 12-byte 0xFF footer.

    Marks header bytes 0x00 and 0x01 as 0x02 (active) for each enabled
    channel, matching what rpsa_client writes for CH1+CH2 (or CH1-only).
    """

    assert ch1.dtype == np.int16
    if ch2 is not None:
        assert ch2.shape == ch1.shape and ch2.dtype == np.int16
        data = ch1.tobytes(order="C") + ch2.tobytes(order="C")
    else:
        data = ch1.tobytes(order="C")
    header = bytearray(PACK_HEADER_SIZE)
    header[0] = 0x02                            # CH1 active
    if ch2 is not None:
        header[1] = 0x02                        # CH2 active
    struct.pack_into("<I", header, DATA_BYTES_OFFSET, len(data))
    return bytes(header) + data + b"\xff" * PACK_FOOTER_SIZE


def synth_rpsa_log(
    num_packs: int,
    samples_per_pack: int,
    fs_hz: int,
    *,
    ch1_total_override: int | None = None,
    ch2_total_override: int | None = None,
    pos_step_override: int | None = None,
    inject_pos_jump_at: int | None = None,
) -> tuple[str, str]:
    """Mimic rpsa_client's .log.txt and .log.lost.txt content.

    Optional overrides are for the smoke-test edge cases that should be
    flagged by metadata.py:
      ch1_total_override / ch2_total_override → CH-symmetry mismatch
      inject_pos_jump_at → at this pack index, Pos jumps by an extra full
      step rather than samples_per_pack (simulates a discontiguous log
      sequence with no `(K)` increment, i.e. an upstream rpsa_client bug).
    """

    total = num_packs * samples_per_pack
    ch1_total = ch1_total_override if ch1_total_override is not None else total
    ch2_total = ch2_total_override if ch2_total_override is not None else total
    log_txt = (
        "======================================================================\n"
        "====   Data transfer report  2026-05-04 00:00:00 ====\n"
        "======================================================================\n\n\n"
        f"Current ADC speed:\t{fs_hz}\n\n\n"
        "Lost data due to file write buffer overflow:\t0\n"
        "Loss of data due to lack of memory:\t0\n\n"
        "Total amount of data transferred:\n"
        f"\t-{(ch1_total + ch2_total)*2}b \n\n"
        "The total amount of data transmitted on: Channel 1\n"
        f"\t-{ch1_total} Samples\n"
        f"\t-{ch1_total*2}b \n"
        "\tLost data on: Channel 1\n"
        "\t- FPGA:\t0 Samples\n\n"
        "The total amount of data transmitted on: Channel 2\n"
        f"\t-{ch2_total} Samples\n"
        f"\t-{ch2_total*2}b \n"
        "\tLost data on: Channel 2\n"
        "\t- FPGA:\t0 Samples\n\n"
    )
    step = pos_step_override if pos_step_override is not None else samples_per_pack
    lost_lines = []
    pos = 0
    for i in range(num_packs):
        lost_lines.append(
            f"Channel 1: Pos {pos} Get: {samples_per_pack} (0)\t"
            f"Channel 2: Pos {pos} Get: {samples_per_pack} (0)\t"
        )
        pos += step
        if inject_pos_jump_at is not None and i == inject_pos_jump_at:
            pos += samples_per_pack          # extra step → Pos becomes non-contiguous
    return log_txt, "\n".join(lost_lines) + "\n"


def _check_missing_log_raises(run_dir: Path, bin_bytes: bytes) -> None:
    """Without log files, parse_rpsa_logs must raise (not silently say no-loss)."""

    bin_path = run_dir / "wave2.bin"
    bin_path.write_bytes(bin_bytes)
    try:
        parse_rpsa_logs(bin_path)
    except RpsaLogError:
        pass
    else:
        raise AssertionError("parse_rpsa_logs should have raised on missing logs")
    bin_path.unlink()


def _check_single_channel_autodetect(run_dir: Path) -> None:
    """A 1-channel capture (CH2 disabled) parses correctly without API tweaks."""

    ch1 = np.arange(8, dtype=np.int16)
    bin_path = run_dir / "wave1ch.bin"
    bin_path.write_bytes(synth_rpsa_pack(ch1, ch2=None))
    n = detect_num_channels(bin_path)
    assert n == 1, f"detect_num_channels returned {n}, expected 1"
    streams = read_streams(bin_path)            # auto-detect
    assert len(streams) == 1
    assert np.array_equal(streams[0], ch1), f"ch1 mismatch: {streams[0]} vs {ch1}"
    bin_path.unlink()


def _check_truncated_midpack_raises(run_dir: Path) -> None:
    """A file truncated inside a pack must raise ValueError, not silently return short data."""

    full = synth_rpsa_pack(np.arange(8, dtype=np.int16), np.arange(8, dtype=np.int16))
    # Cut off the last few bytes of the data area
    truncated = full[: PACK_HEADER_SIZE + 16]
    bin_path = run_dir / "wave_trunc.bin"
    bin_path.write_bytes(truncated)
    try:
        list(iter_interleaved_blocks(bin_path))
    except ValueError:
        pass
    else:
        raise AssertionError("iter_interleaved_blocks should have raised on truncation")
    bin_path.unlink()


def _check_pos_discontinuity(run_dir: Path, bin_bytes: bytes) -> None:
    """A log with non-contiguous Pos (and (K)=0 anyway) must trigger state=lossy."""

    sub = run_dir / "pos_jump"
    sub.mkdir()
    (sub / "waveform.bin").write_bytes(bin_bytes)
    log_txt, log_lost = synth_rpsa_log(
        num_packs=3, samples_per_pack=4, fs_hz=976562, inject_pos_jump_at=1
    )
    (sub / "waveform.bin.log.txt").write_text(log_txt)
    (sub / "waveform.bin.log.lost.txt").write_text(log_lost)
    write_metadata(
        sub, host="t", decimation=128, input_range="LV",
        requested_duration_s=None,
        rpsa_client_argv=[], streaming_server_config={}, interrupted=False,
    )
    sm = json.loads((sub / "summary.json").read_text())
    assert sm["state"] == "lossy", f"pos jump should be lossy, got {sm['state']}"
    assert sm["loss"]["pos_sequence_gaps_total"] > 0


def _check_channel_asymmetry(run_dir: Path, bin_bytes: bytes) -> None:
    """A log where CH1 total != CH2 total must trigger state=lossy."""

    sub = run_dir / "ch_asym"
    sub.mkdir()
    (sub / "waveform.bin").write_bytes(bin_bytes)
    log_txt, log_lost = synth_rpsa_log(
        num_packs=3, samples_per_pack=4, fs_hz=976562,
        ch1_total_override=12, ch2_total_override=8,
    )
    (sub / "waveform.bin.log.txt").write_text(log_txt)
    (sub / "waveform.bin.log.lost.txt").write_text(log_lost)
    write_metadata(
        sub, host="t", decimation=128, input_range="LV",
        requested_duration_s=None,
        rpsa_client_argv=[], streaming_server_config={}, interrupted=False,
    )
    sm = json.loads((sub / "summary.json").read_text())
    assert sm["state"] == "lossy", f"channel asymmetry should be lossy, got {sm['state']}"
    assert sm["loss"]["channel_asymmetry"] is True


def _check_log_pack_count_recorded(run_dir: Path, bin_bytes: bytes,
                                    log_txt: str, log_lost: str) -> None:
    """A clean run reports per-channel pack counts and they match file packs."""

    sub = run_dir / "clean_pack_count"
    sub.mkdir()
    (sub / "waveform.bin").write_bytes(bin_bytes)
    (sub / "waveform.bin.log.txt").write_text(log_txt)
    (sub / "waveform.bin.log.lost.txt").write_text(log_lost)
    write_metadata(
        sub, host="t", decimation=128, input_range="LV",
        requested_duration_s=None,
        rpsa_client_argv=[], streaming_server_config={}, interrupted=False,
    )
    sm = json.loads((sub / "summary.json").read_text())
    assert sm["state"] == "finished"
    assert sm["loss"]["pack_count_per_channel"] == {"1": 3, "2": 3}
    assert sm["loss"]["file_pack_count"] == 3
    assert sm["loss"]["file_pack_count_mismatch"] is False
    assert sm["loss"]["pos_sequence_gaps_total"] == 0


def _check_length_cross_check(run_dir: Path, bin_bytes: bytes, log_txt: str, log_lost: str) -> None:
    """metadata.write_metadata flags state=lossy when file holds fewer samples than -l limit
    and the user did not Ctrl+C."""

    sub = run_dir / "short_run"
    sub.mkdir()
    bin_path = sub / "waveform.bin"
    bin_path.write_bytes(bin_bytes)               # only 10 samples on disk
    (sub / "waveform.bin.log.txt").write_text(log_txt)
    (sub / "waveform.bin.log.lost.txt").write_text(log_lost)
    # Pretend the user requested 100 ms of data, which at 976562.5 Sa/s is
    # ~97 656 samples — far more than the file actually contains.
    write_metadata(
        sub, host="t", decimation=128, input_range="LV",
        requested_duration_s=0.1,
        rpsa_client_argv=[], streaming_server_config={}, interrupted=False,
    )
    sm = json.loads((sub / "summary.json").read_text())
    assert sm["state"] == "lossy", f"expected state=lossy, got {sm['state']}"
    assert sm["loss"]["has_any_loss"] is True
    assert sm["loss"]["file_shortfall_samples"] > 0
    # Same bin, same logs, but interrupted=True: should be 'interrupted', not 'lossy'.
    sub2 = run_dir / "short_run_interrupted"
    sub2.mkdir()
    (sub2 / "waveform.bin").write_bytes(bin_bytes)
    (sub2 / "waveform.bin.log.txt").write_text(log_txt)
    (sub2 / "waveform.bin.log.lost.txt").write_text(log_lost)
    write_metadata(
        sub2, host="t", decimation=128, input_range="LV",
        requested_duration_s=0.1,
        rpsa_client_argv=[], streaming_server_config={}, interrupted=True,
    )
    sm2 = json.loads((sub2 / "summary.json").read_text())
    assert sm2["state"] == "interrupted", f"expected state=interrupted, got {sm2['state']}"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "20260504T000000Z_976562Sa_s"
        run_dir.mkdir()

        # Synthesise a 3-pack rpsa_client capture: 4 + 4 + 2 samples per channel.
        # CH1 = 0,1,2,...,9 ; CH2 = 100,101,...,109. Distinguishable per channel.
        rng = np.arange(10, dtype=np.int16)
        ch1_full = rng                # 10 samples
        ch2_full = rng + 100          # 10 samples
        # Split: 4, 4, 2
        sizes = [4, 4, 2]
        bin_bytes = b""
        offset = 0
        for n in sizes:
            bin_bytes += synth_rpsa_pack(
                ch1_full[offset:offset + n], ch2_full[offset:offset + n]
            )
            offset += n

        bin_path = run_dir / "waveform.bin"
        bin_path.write_bytes(bin_bytes)

        log_txt, log_lost = synth_rpsa_log(num_packs=3, samples_per_pack=4, fs_hz=976562)
        (run_dir / "waveform.bin.log.txt").write_text(log_txt)
        (run_dir / "waveform.bin.log.lost.txt").write_text(log_lost)

        # 1) parser correctness
        ch1, ch2 = read_streams(bin_path)
        assert ch1.shape == (10,), f"ch1 shape {ch1.shape}"
        assert ch2.shape == (10,), f"ch2 shape {ch2.shape}"
        assert np.array_equal(ch1, ch1_full), f"ch1 mismatch {ch1} vs {ch1_full}"
        assert np.array_equal(ch2, ch2_full), f"ch2 mismatch {ch2} vs {ch2_full}"
        assert total_samples_per_channel(bin_path) == 10

        blocks = list(iter_interleaved_blocks(bin_path))
        assert len(blocks) == 3 and [b.shape[0] for b in blocks] == [4, 4, 2]
        joined = np.concatenate([b for b in blocks])
        assert np.array_equal(joined[:, 0], ch1_full)
        assert np.array_equal(joined[:, 1], ch2_full)

        # 2) loss detection
        log = parse_rpsa_logs(bin_path)
        assert log.has_any_loss is False, f"unexpected loss: {log}"
        assert log.adc_speed_hz == 976562.0
        assert all(v == 0 for v in log.fpga_lost_per_channel.values()), log.fpga_lost_per_channel
        assert all(v == 0 for v in log.per_pack_lost), log.per_pack_lost

        # 3) metadata generation
        write_metadata(
            run_dir,
            host="test-host",
            decimation=128,
            input_range="LV",
            requested_duration_s=10 / 976562.5,
            rpsa_client_argv=["-s", "-h", "127.0.0.1", "-f", "bin", "-l", "10"],
            streaming_server_config={"adc_decimation": "128"},
            interrupted=False,
        )
        cfg = json.loads((run_dir / "config.json").read_text())
        sm = json.loads((run_dir / "summary.json").read_text())
        assert cfg["acquisition"]["decimation"] == 128
        assert cfg["board"]["input_range_volts_peak_nominal"] == 1.0
        assert cfg["acquisition"]["effective_sample_rate_hz_per_channel"] == 125_000_000 / 128
        assert sm["state"] == "finished"
        assert sm["loss"]["has_any_loss"] is False
        assert sm["samples_received_per_channel"] == 10

        # 4) raw-counts CSV
        out_csv = run_dir / "raw.csv"
        n = convert_run_to_csv(run_dir, out_csv, max_samples=10)
        assert n == 10
        rows = list(csv.reader(out_csv.open()))
        assert rows[0] == ["sample_index", "time_s", "ch1_raw_i16", "ch2_raw_i16"]
        for i, row in enumerate(rows[1:]):
            assert int(row[0]) == i
            assert int(row[2]) == int(ch1_full[i])
            assert int(row[3]) == int(ch2_full[i])

        # 5) volts CSV
        out_v = run_dir / "volts.csv"
        nv, fs, fsv = convert_run_to_volts_csv(run_dir, out_v, max_samples=10)
        assert nv == 10
        assert fs == 125_000_000 / 128
        assert fsv == 1.0
        rows = list(csv.reader(out_v.open()))
        assert rows[0] == ["time_s", "ch1_volts", "ch2_volts"]
        # ch1[5] = 5 counts → 5/8192 V = 6.103515625e-4 V
        v1 = float(rows[6][1])
        assert abs(v1 - 5.0 / 8192.0) < 1e-12, f"volt conversion off: {v1}"

        # 6) extra-strict checks for the post-review fixes
        _check_missing_log_raises(run_dir, bin_bytes)
        _check_single_channel_autodetect(run_dir)
        _check_truncated_midpack_raises(run_dir)
        _check_length_cross_check(run_dir, bin_bytes, log_txt, log_lost)

        # 7) continuity-review fixes (Pos contiguity, CH symmetry, pack counts)
        _check_pos_discontinuity(run_dir, bin_bytes)
        _check_channel_asymmetry(run_dir, bin_bytes)
        _check_log_pack_count_recorded(run_dir, bin_bytes, log_txt, log_lost)

    print("smoke_rpsa_reader OK")


if __name__ == "__main__":
    main()
