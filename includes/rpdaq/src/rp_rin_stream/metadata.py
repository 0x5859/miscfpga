"""Generate ``config.json`` and ``summary.json`` for an rpsa_client capture.

The launcher invokes this on the PC side after pulling the .bin + .log files
back from the Red Pitaya. It reads the rpsa_client log (loss counters, ADC
speed) and walks the .bin to count actual samples written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rpsa_reader import (
    RpsaLogError,
    RpsaLossReport,
    detect_num_channels,
    parse_rpsa_logs,
    total_samples_per_channel,
)

ADC_BASE_RATE_HZ = 125_000_000.0
SAMPLES_PER_FULL_PACK = 131_072  # rpsa_client / streaming-server default block


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def find_capture_bin(run_dir: Path) -> Path:
    candidates = sorted(run_dir.glob("waveform.bin"))
    if not candidates:
        candidates = sorted(p for p in run_dir.glob("data_file_*.bin") if p.suffix == ".bin")
    if not candidates:
        raise FileNotFoundError(f"No waveform.bin or data_file_*.bin in {run_dir}")
    return candidates[0]


def write_metadata(
    run_dir: Path,
    *,
    host: str,
    decimation: int,
    input_range: str,
    requested_duration_s: float | None,
    rpsa_client_argv: list[str],
    streaming_server_config: dict[str, str],
    interrupted: bool,
) -> tuple[Path, Path]:
    bin_path = find_capture_bin(run_dir)
    fs_hz = ADC_BASE_RATE_HZ / decimation
    file_size = bin_path.stat().st_size
    full_scale_volts = 1.0 if input_range.upper() == "LV" else 20.0
    attenuator = "A_1_1" if input_range.upper() == "LV" else "A_1_20"

    # Parse the .bin defensively. A truncated mid-pack file (e.g. SSH
    # dropped during capture) raises ValueError; treat that as loss rather
    # than crashing the launcher and leaving the user with no summary.
    parse_error: str | None = None
    counts = 0
    detected_channels: int | None = None
    try:
        detected_channels = detect_num_channels(bin_path)
        counts = total_samples_per_channel(bin_path)
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"

    # Parse logs; missing or unparseable logs raise RpsaLogError (this path
    # is intentionally hard so a missing log cannot be silently treated as
    # "no loss").
    log_error: str | None = None
    try:
        log: RpsaLossReport = parse_rpsa_logs(bin_path)
    except RpsaLogError as exc:
        log_error = str(exc)
        log = RpsaLossReport(
            fpga_lost_per_channel={},
            file_buffer_lost=0,
            memory_lost=0,
            per_pack_lost=[],
            pack_records_per_channel={},
            pos_sequence_contiguous_per_channel={},
            pos_sequence_gaps_per_channel={},
            total_samples_per_channel={},
            adc_speed_hz=None,
            log_txt_present=(run_dir / (bin_path.name + ".log.txt")).exists(),
            log_lost_txt_present=(run_dir / (bin_path.name + ".log.lost.txt")).exists(),
        )

    # Length cross-check: requested limit (samples) vs what is in the file.
    # rpsa_client truncates the last pack to honour -l exactly, so a
    # successful finish has counts == limit. Anything less, when not
    # interrupted by the user, is real loss.
    expected_samples: int | None = None
    if requested_duration_s is not None:
        expected_samples = int(round(requested_duration_s * fs_hz))
    short_by = (
        max(0, expected_samples - counts) if expected_samples is not None else 0
    )
    file_short = (
        expected_samples is not None and counts < expected_samples and not interrupted
    )

    # Cross-check vs the channel-1 count rpsa_client reported in its log.
    # rpsa_client may have RECEIVED a few packs more than the file holds
    # (its receiver count includes packs that arrived after -l limit was
    # already satisfied and were therefore truncated when writing). That
    # extra slack is < one full pack. If the gap is larger than one full
    # pack, that's not normal truncation and we flag it as loss.
    log_ch1 = log.total_samples_per_channel.get(1, 0)
    log_minus_file = log_ch1 - counts
    log_says_more_than_one_pack = log_minus_file > SAMPLES_PER_FULL_PACK

    # Channel symmetry check: every enabled channel should report the
    # same total sample count and the same per-pack record count.  An
    # asymmetry here would indicate an upstream issue we don't otherwise
    # catch (a channel disabled mid-run, or rpsa_client dropping a
    # channel from one pack).
    channel_totals = sorted(set(log.total_samples_per_channel.values()))
    channel_pack_counts = sorted(set(log.pack_count_per_channel.values()))
    channel_asymmetry = (
        len(channel_totals) > 1 or len(channel_pack_counts) > 1
    )

    # Cross-check log-pack-count vs file-pack-count.  detect_num_channels
    # returned the number of active channels per pack; the BIN parser
    # naturally walks every pack.  If file_packs != log_packs we know the
    # .bin was truncated outside the rpsa_client write path (e.g. scp
    # truncation that happened to land on a pack boundary).
    log_packs_ch1 = log.pack_count_per_channel.get(1, 0)
    file_packs = 0
    file_packs_error: str | None = None
    if parse_error is None and detected_channels:
        try:
            from .rpsa_reader import iter_packs
            file_packs = sum(1 for _ in iter_packs(bin_path, detected_channels))
        except Exception as exc:
            file_packs_error = f"{type(exc).__name__}: {exc}"
    file_packs_mismatch = (
        log_packs_ch1 > 0
        and file_packs > 0
        and abs(file_packs - log_packs_ch1) > 0
    )

    # Pos-sequence contiguity (per channel).  Strictly redundant with
    # `(K)==0` because rpsa_client uses `Pos += samples + lost`, but
    # we re-derive it as a defensive check against upstream bugs / log
    # corruption.
    pos_contiguous_all = (
        log.pos_sequence_contiguous_per_channel
        and all(log.pos_sequence_contiguous_per_channel.values())
    )
    pos_gap_total = sum(log.pos_sequence_gaps_per_channel.values())

    if (
        log.has_any_loss
        or parse_error
        or log_error
        or file_short
        or log_says_more_than_one_pack
        or channel_asymmetry
        or file_packs_mismatch
        or file_packs_error
        or (log.logs_present and not pos_contiguous_all)
    ):
        state = "lossy"
    elif interrupted:
        state = "interrupted"
    else:
        state = "finished"

    config = {
        "schema": "redpitaya-rin-stream/config-v2-rpsa",
        "created_utc": utc_now_iso(),
        "run_dir": str(run_dir),
        "host": host,
        "board": {
            "model": "RED PITAYA STEMlab 125-14 Z7020-LN",
            "adc_channels": 2,
            "adc_native_bits": 14,
            "adc_base_rate_hz": ADC_BASE_RATE_HZ,
            "fast_input_coupling": "DC",
            "input_range_setting": input_range.upper(),
            "input_range_volts_peak_nominal": full_scale_volts,
            "channel_attenuator_setting": attenuator,
            "note": (
                "Input range LV/HV is hardware-dependent; verify the front-end "
                "attenuator jumpers on the board match the software setting."
            ),
        },
        "acquisition": {
            "method": "rpsa_client",
            "requested_adc_decimation": decimation,
            "decimation": decimation,
            "effective_sample_rate_hz_per_channel": fs_hz,
            "duration_s": requested_duration_s,
            "stop_after_samples_per_channel": (
                None if requested_duration_s is None
                else int(round(requested_duration_s * fs_hz))
            ),
            "channels_enabled": [1, 2],
            "resolution_config": "BIT_16",
            "save_mode": "RAW_ADC_COUNTS",
            "rpsa_client_argv": rpsa_client_argv,
        },
        "storage": {
            "waveform_format": "rpsa_client_bin_v1",
            "waveform_format_note": (
                "Per-pack: 112-byte header + CH1 int16 LE block + CH2 int16 LE "
                "block + 12-byte 0xFF footer. byte 0x68 in header gives data_bytes."
            ),
            "bytes_per_sample_per_channel": 2,
            "frame_bytes": 4,
            "waveform_file_size_bytes": file_size,
            "samples_per_channel": counts,
        },
        "redpitaya_streaming": {
            "python_module_imported": None,
            "rpsa_client_path": "/tmp/rpsa_pylib/rpsa_client",
            "streaming_server_config_sent": streaming_server_config,
        },
        "host_pc": {
            "python": sys.version,
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
    }

    summary = {
        "schema": "redpitaya-rin-stream/final-summary-v3-rpsa",
        "updated_utc": utc_now_iso(),
        "state": state,
        "interrupted": interrupted,
        "samples_received_per_channel": counts,
        "expected_samples_per_channel": expected_samples,
        "duration_recorded_s": counts / fs_hz if fs_hz > 0 else None,
        "detected_active_channels": detected_channels,
        "loss": {
            "has_any_loss": (
                log.has_any_loss
                or bool(parse_error)
                or bool(log_error)
                or file_short
                or log_says_more_than_one_pack
                or channel_asymmetry
                or file_packs_mismatch
                or bool(file_packs_error)
            ),
            "fpga_lost_per_channel": log.fpga_lost_per_channel,
            "file_buffer_lost": log.file_buffer_lost,
            "memory_lost": log.memory_lost,
            "per_pack_lost_total": sum(log.per_pack_lost),
            "per_pack_lost_max": max(log.per_pack_lost) if log.per_pack_lost else 0,
            # Renamed: this counts ALL `(K)` records seen in .log.lost.txt
            # which is `pack_count × num_channels` because each line has
            # CH1 and CH2 records (e.g. 448 packs → 896 records). Use the
            # _per_channel field below for an unambiguous pack count.
            "per_pack_lost_records_total": len(log.per_pack_lost),
            "pack_count_per_channel": log.pack_count_per_channel,
            "file_pack_count": file_packs,
            "file_pack_count_error": file_packs_error,
            "logs_present": log.logs_present,
            "log_parse_error": log_error,
            "bin_parse_error": parse_error,
            "file_shortfall_samples": short_by if file_short else 0,
            "log_minus_file_samples": log_minus_file,
            # New cross-checks (all should be False / 0 for a clean run)
            "channel_asymmetry": channel_asymmetry,
            "channel_total_samples_seen": channel_totals,
            "channel_pack_counts_seen": channel_pack_counts,
            "file_pack_count_mismatch": file_packs_mismatch,
            "pos_sequence_contiguous_per_channel": log.pos_sequence_contiguous_per_channel,
            "pos_sequence_gaps_per_channel": log.pos_sequence_gaps_per_channel,
            "pos_sequence_gaps_total": pos_gap_total,
        },
        "rpsa_client_log": {
            "adc_speed_hz_reported": log.adc_speed_hz,
            "samples_per_channel_reported": log.total_samples_per_channel,
        },
        "files": {
            "waveform_bin": bin_path.name,
            "waveform_bin_size_bytes": file_size,
            "log_txt": (bin_path.name + ".log.txt") if (
                run_dir / (bin_path.name + ".log.txt")
            ).exists() else None,
            "log_lost_txt": (bin_path.name + ".log.lost.txt") if (
                run_dir / (bin_path.name + ".log.lost.txt")
            ).exists() else None,
        },
    }

    config_path = run_dir / "config.json"
    summary_path = run_dir / "summary.json"
    atomic_write_json(config_path, config)
    atomic_write_json(summary_path, summary)
    return config_path, summary_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate config.json + summary.json for an rpsa_client capture."
    )
    p.add_argument("run_dir")
    p.add_argument("--host", required=True)
    p.add_argument("--decimation", type=int, required=True)
    p.add_argument("--input-range", choices=["LV", "HV"], required=True)
    p.add_argument("--duration", type=float, default=None,
                   help="Requested duration in seconds (None = unlimited)")
    p.add_argument("--interrupted", action="store_true",
                   help="Set state=interrupted instead of finished")
    p.add_argument("--rpsa-client-argv", default="",
                   help="Comma-separated argv that was passed to rpsa_client")
    p.add_argument("--streaming-server-config", default="{}",
                   help="JSON dict of sendConfig key=value pairs sent to server")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = Path(args.run_dir)
    rpsa_argv = [s for s in args.rpsa_client_argv.split(",") if s] if args.rpsa_client_argv else []
    server_cfg = json.loads(args.streaming_server_config) if args.streaming_server_config else {}
    cfg_p, sum_p = write_metadata(
        run_dir,
        host=args.host,
        decimation=args.decimation,
        input_range=args.input_range,
        requested_duration_s=args.duration,
        rpsa_client_argv=rpsa_argv,
        streaming_server_config=server_cfg,
        interrupted=args.interrupted,
    )
    print(f"wrote {cfg_p}")
    print(f"wrote {sum_p}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
