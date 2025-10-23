"""Convert an rpsa_client-format chunked binary capture into raw-counts CSV.

Output columns: ``sample_index, time_s, ch1_raw_i16, ch2_raw_i16``.

The input is the single ``data_file_*.bin`` produced on the Red Pitaya by
``rpsa_client -s -f bin``; the parser is in ``rpsa_reader``. ``time_s`` is
``sample_index / Fs`` where Fs is read from the run's ``config.json``.

Use ``--start-sample`` / ``--max-samples`` to export a slice — full captures
expand to ~30 bytes/row.
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


def _open_text_output(path: str | Path) -> IO[str]:
    path = Path(path)
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "wt", encoding="utf-8", newline="")
    return path.open("w", encoding="utf-8", newline="")


def find_capture_bin(run_dir: Path) -> Path:
    """Locate the rpsa_client .bin in a run directory.

    The launcher renames the rpsa_client output to ``waveform.bin`` for
    stability; older directories may still hold the original
    ``data_file_*.bin`` name.
    """

    candidates = sorted(run_dir.glob("waveform.bin"))
    if not candidates:
        candidates = sorted(p for p in run_dir.glob("data_file_*.bin") if p.suffix == ".bin")
    if not candidates:
        raise FileNotFoundError(
            f"No waveform.bin or data_file_*.bin in {run_dir}"
        )
    return candidates[0]


def load_effective_sample_rate(run_dir: Path) -> float:
    cfg_path = run_dir / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return float(cfg["acquisition"]["effective_sample_rate_hz_per_channel"])


def convert_run_to_csv(
    run_dir: str | Path,
    out_csv: str | Path,
    *,
    start_sample: int = 0,
    max_samples: int | None = None,
) -> int:
    run_dir = Path(run_dir)
    fs = load_effective_sample_rate(run_dir)
    bin_path = find_capture_bin(run_dir)

    written = 0
    global_sample = 0
    stop_at = None if max_samples is None else start_sample + max_samples

    with _open_text_output(out_csv) as f_out:
        writer = csv.writer(f_out)
        writer.writerow(["sample_index", "time_s", "ch1_raw_i16", "ch2_raw_i16"])

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
                return written
            idx = np.arange(
                block_start + local_start,
                block_start + local_end,
                dtype=np.int64,
            )
            t = idx.astype(np.float64) / fs
            sliced = block[local_start:local_end]
            writer.writerows(
                zip(
                    idx.tolist(),
                    t.tolist(),
                    sliced[:, 0].tolist(),
                    sliced[:, 1].tolist(),
                )
            )
            written += local_end - local_start
            global_sample = block_end
            if stop_at is not None and global_sample >= stop_at:
                return written

    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert Red Pitaya rpsa_client BIN to raw-counts CSV.")
    p.add_argument("run_dir", help="Run directory containing config.json + waveform.bin")
    p.add_argument("--out", required=True, help="Output CSV path. Use .gz for gzip.")
    p.add_argument("--start-sample", type=int, default=0)
    p.add_argument("--max-samples", type=int, default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.start_sample < 0:
        raise SystemExit("--start-sample must be >= 0")
    if args.max_samples is not None and args.max_samples < 0:
        raise SystemExit("--max-samples must be >= 0")
    n = convert_run_to_csv(
        args.run_dir, args.out,
        start_sample=args.start_sample,
        max_samples=args.max_samples,
    )
    print(f"Wrote {n:,} samples/channel to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
