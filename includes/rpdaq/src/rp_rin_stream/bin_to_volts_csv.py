"""Convert an rpsa_client-format chunked binary capture into a time/voltage CSV.

Output columns: ``time_s, ch1_volts, ch2_volts``.

Reads the ADC input range from the run's ``config.json`` so LV (±1 V) and
HV (±20 V) captures both produce calibrated volts.

For long captures use ``--start-sample`` / ``--max-samples`` to export a
slice; the underlying parser walks the file pack-by-pack so memory stays
bounded regardless of total length.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path
from typing import IO

import numpy as np

from .rpsa_reader import iter_interleaved_blocks

# Signed 14-bit ADC stored as int16: ±8192 counts ↔ ±full_scale_volts.
ADC_HALF_SCALE_COUNTS = 8192


def _open_text_output(path: str | Path) -> IO[str]:
    path = Path(path)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "wt", encoding="utf-8", newline="")
    return path.open("w", encoding="utf-8", newline="")


def find_capture_bin(run_dir: Path) -> Path:
    candidates = sorted(run_dir.glob("waveform.bin"))
    if not candidates:
        candidates = sorted(p for p in run_dir.glob("data_file_*.bin") if p.suffix == ".bin")
    if not candidates:
        raise FileNotFoundError(f"No waveform.bin or data_file_*.bin in {run_dir}")
    return candidates[0]


def load_run_metadata(run_dir: Path) -> tuple[float, float]:
    """Return (effective_sample_rate_hz, input_range_volts_peak)."""

    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    fs = float(cfg["acquisition"]["effective_sample_rate_hz_per_channel"])
    full_scale = float(cfg["board"]["input_range_volts_peak_nominal"])
    return fs, full_scale


def convert_run_to_volts_csv(
    run_dir: str | Path,
    out_csv: str | Path,
    *,
    start_sample: int = 0,
    max_samples: int | None = None,
) -> tuple[int, float, float]:
    """Stream the rpsa_client .bin into a time/voltage CSV.

    Returns (samples_written_per_channel, fs_hz, full_scale_volts).
    """

    run_dir = Path(run_dir)
    fs, full_scale = load_run_metadata(run_dir)
    counts_to_volts = full_scale / ADC_HALF_SCALE_COUNTS
    bin_path = find_capture_bin(run_dir)

    written = 0
    global_sample = 0
    stop_at = None if max_samples is None else start_sample + max_samples

    with _open_text_output(out_csv) as f_out:
        writer = csv.writer(f_out)
        writer.writerow(["time_s", "ch1_volts", "ch2_volts"])

        for block in iter_interleaved_blocks(bin_path, num_channels=2):
            n = block.shape[0]
            block_start = global_sample
            block_end = global_sample + n
            if block_end <= start_sample:
                global_sample = block_end
                continue
            local_start = max(0, start_sample - block_start)
            local_end = n if stop_at is None else min(n, stop_at - block_start)
            if local_end <= local_start:
                return written, fs, full_scale
            idx = np.arange(
                block_start + local_start,
                block_start + local_end,
                dtype=np.int64,
            )
            t = idx.astype(np.float64) / fs
            volts = block[local_start:local_end].astype(np.float64) * counts_to_volts
            writer.writerows(zip(t.tolist(), volts[:, 0].tolist(), volts[:, 1].tolist()))
            written += local_end - local_start
            global_sample = block_end
            if stop_at is not None and global_sample >= stop_at:
                return written, fs, full_scale

    return written, fs, full_scale


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert Red Pitaya rpsa_client BIN to a time/voltage CSV."
    )
    p.add_argument("run_dir", help="Run directory containing config.json + waveform.bin")
    p.add_argument("--out", required=True, help="Output CSV path. Use .gz for gzip.")
    p.add_argument("--start-sample", type=int, default=0)
    p.add_argument("--max-samples", type=int, default=None,
                   help="Default: whole capture (very large for full-rate runs).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.start_sample < 0:
        raise SystemExit("--start-sample must be >= 0")
    if args.max_samples is not None and args.max_samples < 0:
        raise SystemExit("--max-samples must be >= 0")
    n, fs, full_scale = convert_run_to_volts_csv(
        args.run_dir, args.out,
        start_sample=args.start_sample,
        max_samples=args.max_samples,
    )
    print(f"Wrote {n:,} samples/channel to {args.out}")
    print(
        f"Fs={fs:,.1f} Sa/s/ch  |  full-scale=±{full_scale} V  |  "
        f"LSB ≈ {full_scale / ADC_HALF_SCALE_COUNTS * 1e6:.2f} µV"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
