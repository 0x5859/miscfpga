#!/usr/bin/env python3
"""
RIN single-channel and two-channel cross-spectrum estimator.

Input: two synchronous voltage records from two independent photoreceivers.
Output: one-sided / SSB RIN spectra in dBc/Hz.

The calculation is band-by-band, with a user-editable RBW table.  RBW here is
Hann-window ENBW (equivalent noise bandwidth), not merely FFT bin spacing.

Usage
=====

1) Command line (writes ``<prefix>.csv``, ``<prefix>.png``, ``<prefix>.pdf``)::

       # rp_rin_stream run dir (fs and full-scale auto-loaded from config.json)
       python rin_cross_spectrum.py --rpsa-run runs/my_capture \\
           --dc1 0.5 --dc2 0.5 --out-prefix /tmp/rin

       # Plain two-channel file (.npy/.npz/.csv/.txt) -- fs is required
       python rin_cross_spectrum.py --file data.npy --fs 1e6 \\
           --dc1 0.5 --dc2 0.5 --out-prefix /tmp/rin

       # Common knobs
       #   --rbw-scale 4         -> wider RBW per band, more averaging
       #   --rpsa-skip-seconds 1 -> drop first 1 s of an rpsa-run capture
       #   --rpsa-max-seconds 10 -> only analyze the next 10 s
       #   --no-plot-cross-real  -> hide Re{S_12} curve in the figure

2) From another Python program (gets the plotting data back as a dict)::

       import sys
       from rin_cross_spectrum import parse_args, rin_cross_spectrum

       # Build a fully-populated Namespace by parsing an explicit argv.
       # parse_args() reads sys.argv directly, so monkey-patch it first --
       # rin_cross_spectrum() requires *every* attribute parse_args() sets.
       sys.argv = [
           "rin_cross_spectrum",
           "--rpsa-run", "runs/my_capture",
           "--dc1", "0.5", "--dc2", "0.5",
           "--out-prefix", "/tmp/rin",
       ]
       args = parse_args()
       # Optionally tweak fields after parsing:
       args.rpsa_max_seconds = 10.0

       out = rin_cross_spectrum(args=args)
       # out["result"]["f_Hz"], out["result"]["ch1_dBc_per_Hz"], ...
       # out["out_csv"], out["out_png"], out["out_pdf"]
       # out["meta"], out["dc1"], out["dc2"], out["rbw_by_band_hz"], ...

   Or skip ``args=`` entirely to drive everything from ``sys.argv``::

       from rin_cross_spectrum import rin_cross_spectrum
       out = rin_cross_spectrum()  # equivalent to running as a script

   Either way, the same CSV/PNG/PDF files are written and the same
   ``Input summary`` / ``RBW / averaging summary`` / ``Saved:`` lines are
   printed -- the only difference is that the function also returns a
   dict with the spectrum arrays, output paths, and run metadata.

See ``rin_cross_spectrum.__doc__`` for the exact list of returned keys.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np

# matplotlib is imported lazily inside make_plot so --help and pure-CSV
# runs don't require it to be installed.

# -----------------------------------------------------------------------------
# User-editable default RBW table.
# Keys are frequency bands in Hz: (low_inclusive, high_exclusive except last).
# Values are target RBW in Hz, interpreted as Hann-window ENBW.
# For fs = 1 MSa/s, these correspond approximately to segment lengths:
# 0.25 Hz -> 6.0 s, 1 Hz -> 1.5 s, 5 Hz -> 0.30 s,
# 20 Hz -> 75 ms, 100 Hz -> 15 ms.
# -----------------------------------------------------------------------------
RBW_BY_BAND_HZ = OrderedDict({
    (1.0, 10.0): 0.25,
    (10.0, 100.0): 1.0,
    (100.0, 1.0e3): 5.0,
    (1.0e3, 1.0e4): 20.0,
    (1.0e4, 1.0e5): 100.0,
})

# SSB convention:
#   "optical_rin": dBc/Hz = 10*log10(S_RIN_one_sided), common for direct RIN.
#   "rf_am_l":     L_AM(f) = 0.5*S_RIN_one_sided, i.e. 3.0103 dB lower.
DEFAULT_SSB_CONVENTION = "optical_rin"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute single-channel and cross-spectrum RIN from two voltage time series."
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--file", "-i", type=str, default=None,
                     help="Input file: .npy/.npz/.csv/.txt with two voltage columns or two rows.")
    src.add_argument("--rpsa-run", type=str, default=None,
                     help="Path to an rp_rin_stream run directory (containing waveform.bin and config.json). "
                          "fs and full-scale voltage are auto-loaded from config.json.")
    p.add_argument("--rpsa-skip-seconds", type=float, default=0.0,
                   help="Drop the first N seconds of an --rpsa-run capture before analysis (warm-up rejection).")
    p.add_argument("--rpsa-max-seconds", type=float, default=None,
                   help="Limit analysis window to this many seconds after --rpsa-skip-seconds (default: full record).")
    p.add_argument("--fs", type=float, default=None,
                   help="Sampling rate in Sa/s, e.g. 1e6. Required for --file; ignored for --rpsa-run.")
    p.add_argument("--cols", type=int, nargs=2, default=(0, 1),
                   help="For 2D column-style files, channel column indices, default: 0 1.")
    p.add_argument("--skiprows", type=int, default=0,
                   help="Rows to skip for CSV/TXT input.")
    p.add_argument("--delimiter", type=str, default=",",
                   help="Delimiter for CSV/TXT input. Use 'space' for whitespace.")
    p.add_argument("--dc1", type=float, default=None,
                   help="Channel 1 optical-signal DC voltage used for normalization. If omitted, mean(ch1) is used.")
    p.add_argument("--dc2", type=float, default=None,
                   help="Channel 2 optical-signal DC voltage used for normalization. If omitted, mean(ch2) is used.")
    p.add_argument("--dark1", type=float, default=0.0,
                   help="Channel 1 dark/electrical offset voltage. Used only if --dc1 is omitted: dc1=mean(ch1)-dark1.")
    p.add_argument("--dark2", type=float, default=0.0,
                   help="Channel 2 dark/electrical offset voltage. Used only if --dc2 is omitted: dc2=mean(ch2)-dark2.")
    p.add_argument("--overlap", type=float, default=0.5,
                   help="Welch overlap fraction, default: 0.5. Raising to "
                        "0.75/0.875 multiplies the raw segment count by ~2/~4 "
                        "but the effective averaging gain past 50%% Hann "
                        "overlap is only ~1.1x because adjacent segments are "
                        "correlated (Welch 1967). 0.5 is the textbook optimum "
                        "for Hann.")
    p.add_argument("--rbw-scale", type=float, default=1.0,
                   help="Global multiplier on every RBW_BY_BAND_HZ entry. "
                        "Larger = wider Hann ENBW per band, shorter nperseg, "
                        "more N_avg → smoother spectrum and lower 1/sqrt(N_avg) "
                        "*random-error* floor on the cross-spectrum, at the "
                        "cost of coarser frequency resolution. Try "
                        "--rbw-scale 4 to roughly quadruple averaging. Note: "
                        "this only lowers the *uncorrelated* cross floor; any "
                        "real common-mode contribution does not integrate "
                        "down. (Default 1.0.)")
    p.add_argument("--delay-sec", type=float, default=0.0,
                   help="Optional delay correction. Positive means ch2 lags ch1 by this many seconds.")
    p.add_argument("--no-detrend", action="store_true",
                   help="Do not subtract each segment mean. Default subtracts segment mean before FFT.")
    p.add_argument("--ssb-convention", choices=["optical_rin", "rf_am_l"],
                   default=DEFAULT_SSB_CONVENTION,
                   help="SSB convention. optical_rin is standard one-sided direct RIN; rf_am_l subtracts 3.0103 dB.")
    p.add_argument("--out-prefix", type=str, default="rin_result",
                   help="Output prefix for CSV/PDF/PNG.")
    p.add_argument("--plot-abs-cross", action="store_true",
                   help="Also plot |cross spectrum|. It is always positive but biased when correlation has not converged.")
    p.add_argument("--no-plot-cross-real", action="store_true",
                   help="Suppress the Re{S_12} curve in the top RIN panel. "
                        "The CSV still contains it; only the plot is affected. "
                        "Useful when CH1/CH2 are not measuring the same beam "
                        "and the cross is not the headline quantity.")
    p.add_argument("--plot-title", type=str, default=None,
                   help="Override figure suptitle (default: built from data source).")
    return p.parse_args()


def load_rpsa_run(run_dir: str,
                  skip_seconds: float = 0.0,
                  max_seconds: float | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Load CH1+CH2 voltage arrays from an rp_rin_stream run directory.

    Reads ``waveform.bin`` via :mod:`rp_rin_stream.rpsa_reader`, converts the
    raw int16 ADC counts to volts using the saved full-scale-V, and applies
    any user-requested skip/limit window. Returns ``(ch1_v, ch2_v, meta)``.
    """
    src_dir = str(Path(__file__).resolve().parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from rp_rin_stream.rpsa_reader import read_streams  # noqa: WPS433

    run_path = Path(run_dir)
    cfg_path = run_path / "config.json"
    bin_path = run_path / "waveform.bin"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Missing config.json in run dir: {run_path}")
    if not bin_path.is_file():
        raise FileNotFoundError(f"Missing waveform.bin in run dir: {run_path}")
    cfg = json.loads(cfg_path.read_text())
    fs = float(cfg["acquisition"]["effective_sample_rate_hz_per_channel"])
    full_scale_v = float(cfg["board"]["input_range_volts_peak_nominal"])
    counts_to_v = full_scale_v / 8192.0  # signed 14-bit, ±FS_V mapped to ±8192 counts

    ch1_int, ch2_int = read_streams(bin_path)
    n_total = min(ch1_int.size, ch2_int.size)
    n_skip = int(round(max(0.0, skip_seconds) * fs))
    n_skip = min(n_skip, n_total)
    if max_seconds is not None and max_seconds > 0.0:
        n_keep = int(round(max_seconds * fs))
        n_end = min(n_total, n_skip + n_keep)
    else:
        n_end = n_total
    ch1_v = ch1_int[n_skip:n_end].astype(np.float64) * counts_to_v
    ch2_v = ch2_int[n_skip:n_end].astype(np.float64) * counts_to_v

    meta = {
        "run_dir": str(run_path),
        "fs_hz": fs,
        "full_scale_v": full_scale_v,
        "counts_to_v": counts_to_v,
        "samples_total": int(n_total),
        "samples_used": int(ch1_v.size),
        "skip_samples": int(n_skip),
        "decimation": int(cfg["acquisition"].get("decimation", -1)),
        "duration_s_total": float(n_total) / fs,
        "duration_s_used": float(ch1_v.size) / fs,
    }
    return ch1_v, ch2_v, meta


def load_two_channel_file(path: str, cols: Tuple[int, int], skiprows: int, delimiter: str):
    """Return ch1, ch2 as numpy arrays or memmap views where possible."""
    path_obj = Path(path)
    suffix = path_obj.suffix.lower()

    if suffix == ".npy":
        arr = np.load(path_obj, mmap_mode="r")
        if arr.ndim != 2:
            raise ValueError(".npy input must be a 2D array with shape (N,2) or (2,N).")
        # Prefer column format (N, channels). If shape is (2,N), use rows.
        if arr.shape[1] >= max(cols) + 1:
            return arr[:, cols[0]], arr[:, cols[1]]
        if arr.shape[0] >= max(cols) + 1:
            return arr[cols[0], :], arr[cols[1], :]
        raise ValueError(f"Cannot select columns/rows {cols} from array shape {arr.shape}.")

    if suffix == ".npz":
        z = np.load(path_obj)
        keys = list(z.keys())
        if "ch1" in z and "ch2" in z:
            return z["ch1"], z["ch2"]
        arr = z[keys[0]]
        if arr.ndim != 2:
            raise ValueError(".npz first array must be 2D, or contain arrays named 'ch1' and 'ch2'.")
        if arr.shape[1] >= max(cols) + 1:
            return arr[:, cols[0]], arr[:, cols[1]]
        if arr.shape[0] >= max(cols) + 1:
            return arr[cols[0], :], arr[cols[1], :]
        raise ValueError(f"Cannot select columns/rows {cols} from array shape {arr.shape}.")

    if suffix in {".csv", ".txt", ".dat"}:
        delim = None if delimiter == "space" else delimiter
        arr = np.loadtxt(path_obj, delimiter=delim, skiprows=skiprows, usecols=cols)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("CSV/TXT load did not return two columns.")
        return arr[:, 0], arr[:, 1]

    raise ValueError("Unsupported input file type. Use .npy, .npz, .csv, .txt, or .dat.")


def hann_enbw_hz(fs: float, n: int) -> float:
    if n < 2:
        return np.nan
    w = np.hanning(n)
    return fs * np.sum(w * w) / (np.sum(w) ** 2)


def choose_nperseg_for_rbw(fs: float, target_rbw_hz: float, n_total: int) -> int:
    """Choose an even nperseg so that Hann ENBW is close to target_rbw_hz."""
    if target_rbw_hz <= 0:
        raise ValueError("RBW must be positive.")
    # Hann ENBW is approximately 1.5 * fs / n.
    n = int(round(1.5 * fs / target_rbw_hz))
    n = max(n, 8)
    n = min(n, int(n_total))
    # Prefer even length for conventional rfft Nyquist handling.
    if n % 2 == 1 and n > 8:
        n -= 1
    return n


def positive_db10(x: np.ndarray) -> np.ndarray:
    """10log10(x), returning NaN for non-positive values."""
    x = np.asarray(x)
    y = np.full(x.shape, np.nan, dtype=np.float64)
    mask = x > 0
    y[mask] = 10.0 * np.log10(x[mask])
    return y


def estimate_band(
    v1,
    v2,
    fs: float,
    dc1: float,
    dc2: float,
    flo: float,
    fhi: float,
    target_rbw_hz: float,
    include_high_edge: bool,
    overlap: float = 0.5,
    detrend_constant: bool = True,
    delay_sec: float = 0.0,
):
    """Estimate PSD/CSD only for FFT bins in one frequency band."""
    n_total = min(len(v1), len(v2))
    nperseg = choose_nperseg_for_rbw(fs, target_rbw_hz, n_total)
    if nperseg > n_total:
        nperseg = n_total
    if nperseg < 8:
        raise ValueError("Record is too short for spectral estimation.")

    step = int(round(nperseg * (1.0 - overlap)))
    step = max(step, 1)
    starts = np.arange(0, n_total - nperseg + 1, step, dtype=np.int64)
    if starts.size == 0:
        starts = np.array([0], dtype=np.int64)

    df = fs / nperseg
    k_nyq = nperseg // 2
    # Build a small candidate index set and then mask exactly.
    k0 = max(1, int(np.floor(flo / df)) - 2)  # exclude DC; RIN interest starts at 1 Hz
    k1 = min(k_nyq, int(np.ceil(fhi / df)) + 2)
    k = np.arange(k0, k1 + 1, dtype=np.int64)
    f = k * df
    if include_high_edge:
        mask = (f >= flo) & (f <= fhi)
    else:
        mask = (f >= flo) & (f < fhi)
    k = k[mask]
    f = f[mask]
    if k.size == 0:
        raise ValueError(
            f"No FFT bins in band {flo:g}-{fhi:g} Hz. "
            f"Try smaller RBW. Current df={df:g} Hz, nperseg={nperseg}."
        )

    window = np.hanning(nperseg).astype(np.float64)
    sum_w2 = float(np.sum(window * window))
    enbw = fs * sum_w2 / (float(np.sum(window)) ** 2)
    norm = fs * sum_w2

    onesided = np.full(k.shape, 2.0, dtype=np.float64)
    onesided[k == 0] = 1.0
    if nperseg % 2 == 0:
        onesided[k == k_nyq] = 1.0

    acc_p11 = np.zeros(k.size, dtype=np.float64)
    acc_p22 = np.zeros(k.size, dtype=np.float64)
    acc_p12 = np.zeros(k.size, dtype=np.complex128)
    used = 0

    for start in starts:
        s = int(start)
        a = np.asarray(v1[s:s + nperseg], dtype=np.float64)
        b = np.asarray(v2[s:s + nperseg], dtype=np.float64)

        # Convert volts to fractional intensity fluctuation.  dc1/dc2 must be
        # the optical-signal DC voltage, ideally laser-on mean minus dark offset.
        x = (a - dc1) / dc1
        y = (b - dc2) / dc2

        if detrend_constant:
            x = x - np.mean(x)
            y = y - np.mean(y)

        X = np.fft.rfft(x * window)
        Y = np.fft.rfft(y * window)
        Xk = X[k]
        Yk = Y[k]

        acc_p11 += onesided * (np.abs(Xk) ** 2) / norm
        acc_p22 += onesided * (np.abs(Yk) ** 2) / norm
        # Complex cross-spectrum.  With this convention, positive delay_sec
        # means ch2 lags ch1 and is corrected by exp(-j*2*pi*f*delay_sec).
        acc_p12 += onesided * (Xk * np.conj(Yk)) / norm
        used += 1

    p11 = acc_p11 / used
    p22 = acc_p22 / used
    p12 = acc_p12 / used
    if delay_sec != 0.0:
        p12 = p12 * np.exp(-1j * 2.0 * np.pi * f * delay_sec)

    coh = np.abs(p12) ** 2 / np.maximum(p11 * p22, np.finfo(float).tiny)
    coh = np.minimum(coh, 1.0)  # numerical guard

    return {
        "f_Hz": f,
        "p11": p11,
        "p22": p22,
        "p12": p12,
        "coherence": coh,
        "phase_deg": np.angle(p12, deg=True),
        "rbw_enbw_Hz": np.full(f.shape, enbw),
        "df_Hz": np.full(f.shape, df),
        "nperseg": np.full(f.shape, nperseg, dtype=np.int64),
        "n_avg": np.full(f.shape, used, dtype=np.int64),
        "summary": {
            "flo": flo, "fhi": fhi, "target_rbw": target_rbw_hz,
            "actual_enbw": enbw, "df": df, "nperseg": nperseg, "n_avg": used,
            "n_bins": k.size,
        }
    }


def compute_rin(
    v1,
    v2,
    fs: float,
    dc1: float,
    dc2: float,
    rbw_by_band_hz=RBW_BY_BAND_HZ,
    overlap: float = 0.5,
    detrend_constant: bool = True,
    delay_sec: float = 0.0,
    ssb_convention: str = DEFAULT_SSB_CONVENTION,
):
    if not (0.0 <= overlap < 1.0):
        raise ValueError("overlap must satisfy 0 <= overlap < 1.")
    if fs <= 0:
        raise ValueError("Sampling rate fs must be positive.")
    if abs(dc1) <= 10 * np.finfo(float).eps or abs(dc2) <= 10 * np.finfo(float).eps:
        raise ValueError("dc1/dc2 are too close to zero. Provide optical-signal DC voltages.")

    if ssb_convention == "optical_rin":
        ssb_factor = 1.0
    elif ssb_convention == "rf_am_l":
        ssb_factor = 0.5
    else:
        raise ValueError("Unknown SSB convention.")

    chunks = []
    summaries = []
    bands = list(rbw_by_band_hz.items())
    nyq = fs / 2.0
    for i, ((flo, fhi), rbw) in enumerate(bands):
        if flo >= nyq:
            print(f"Warning: skipping band {flo:g}-{fhi:g} Hz because it is above Nyquist {nyq:g} Hz.")
            continue
        fhi_eff = min(fhi, nyq)
        include_hi = (i == len(bands) - 1) or (fhi_eff >= nyq)
        try:
            result = estimate_band(
                v1, v2, fs, dc1, dc2, flo, fhi_eff, rbw,
                include_high_edge=include_hi,
                overlap=overlap,
                detrend_constant=detrend_constant,
                delay_sec=delay_sec,
            )
        except ValueError as exc:
            # Most common cause: --rbw-scale made the band's nperseg coarse
            # enough that no FFT bins fall inside [flo, fhi]. Skip rather
            # than abort the whole run so the other (coarser) bands above
            # still get analyzed.
            print(
                f"Warning: skipping band {flo:g}-{fhi_eff:g} Hz "
                f"(target RBW {rbw:g} Hz): {exc}"
            )
            continue
        chunks.append(result)
        summaries.append(result["summary"])

    if not chunks:
        raise ValueError("No frequency bands could be estimated. Check fs and RBW_BY_BAND_HZ.")

    f = np.concatenate([c["f_Hz"] for c in chunks])
    p11 = np.concatenate([c["p11"] for c in chunks])
    p22 = np.concatenate([c["p22"] for c in chunks])
    p12 = np.concatenate([c["p12"] for c in chunks])
    coh = np.concatenate([c["coherence"] for c in chunks])
    phase_deg = np.concatenate([c["phase_deg"] for c in chunks])
    rbw_enbw = np.concatenate([c["rbw_enbw_Hz"] for c in chunks])
    df_hz = np.concatenate([c["df_Hz"] for c in chunks])
    nperseg = np.concatenate([c["nperseg"] for c in chunks])
    n_avg = np.concatenate([c["n_avg"] for c in chunks])

    order = np.argsort(f)
    f = f[order]
    p11 = p11[order]
    p22 = p22[order]
    p12 = p12[order]
    coh = coh[order]
    phase_deg = phase_deg[order]
    rbw_enbw = rbw_enbw[order]
    df_hz = df_hz[order]
    nperseg = nperseg[order]
    n_avg = n_avg[order]

    # Apply SSB convention only for the displayed dBc/Hz values.  The linear
    # columns p11/p22/p12 below remain one-sided fractional PSDs in 1/Hz.
    ch1_db = positive_db10(ssb_factor * p11)
    ch2_db = positive_db10(ssb_factor * p22)
    cross_real = np.real(p12)
    cross_abs = np.abs(p12)
    cross_real_db = positive_db10(ssb_factor * cross_real)
    cross_abs_db = positive_db10(ssb_factor * cross_abs)

    return {
        "f_Hz": f,
        "S_rin_ch1_1_per_Hz": p11,
        "S_rin_ch2_1_per_Hz": p22,
        "S_rin_cross_complex_1_per_Hz": p12,
        "S_rin_cross_real_1_per_Hz": cross_real,
        "S_rin_cross_abs_1_per_Hz": cross_abs,
        "ch1_dBc_per_Hz": ch1_db,
        "ch2_dBc_per_Hz": ch2_db,
        "cross_real_dBc_per_Hz": cross_real_db,
        "cross_abs_dBc_per_Hz": cross_abs_db,
        "coherence": coh,
        "cross_phase_deg": phase_deg,
        "rbw_enbw_Hz": rbw_enbw,
        "df_Hz": df_hz,
        "nperseg": nperseg,
        "n_avg": n_avg,
        "summaries": summaries,
        "ssb_convention": ssb_convention,
        "ssb_factor": ssb_factor,
    }


def save_csv(result: dict, out_csv: str):
    header = [
        "f_Hz",
        "ch1_RIN_dBc_per_Hz",
        "ch2_RIN_dBc_per_Hz",
        "cross_real_RIN_dBc_per_Hz",
        "cross_abs_RIN_dBc_per_Hz_biased_positive",
        "S_rin_ch1_one_sided_1_per_Hz",
        "S_rin_ch2_one_sided_1_per_Hz",
        "S_rin_cross_real_one_sided_1_per_Hz",
        "S_rin_cross_abs_one_sided_1_per_Hz",
        "cross_phase_deg",
        "coherence",
        "rbw_enbw_Hz",
        "fft_bin_spacing_Hz",
        "nperseg",
        "n_avg_segments",
    ]
    data_cols = [
        result["f_Hz"],
        result["ch1_dBc_per_Hz"],
        result["ch2_dBc_per_Hz"],
        result["cross_real_dBc_per_Hz"],
        result["cross_abs_dBc_per_Hz"],
        result["S_rin_ch1_1_per_Hz"],
        result["S_rin_ch2_1_per_Hz"],
        result["S_rin_cross_real_1_per_Hz"],
        result["S_rin_cross_abs_1_per_Hz"],
        result["cross_phase_deg"],
        result["coherence"],
        result["rbw_enbw_Hz"],
        result["df_Hz"],
        result["nperseg"],
        result["n_avg"],
    ]
    arr = np.column_stack(data_cols)
    np.savetxt(out_csv, arr, delimiter=",", header=",".join(header), comments="")


# Scientific-plotting style helpers live in :mod:`rp_rin_stream._sciplot`.
# Imported lazily inside :func:`make_plot` so ``--help`` doesn't require
# matplotlib.


def make_plot(result: dict,
              out_png: str,
              plot_abs_cross: bool = False,
              plot_cross_real: bool = True,
              meta: dict | None = None,
              dc1: float | None = None,
              dc2: float | None = None,
              ch1_stats: dict | None = None,
              ch2_stats: dict | None = None,
              suptitle: str | None = None,
              out_pdf: str | None = None):
    """Render the RIN figure in the scientific-plotting house style.

    Implementation: vendored helpers from
    :mod:`rp_rin_stream._sciplot` (which mirror the
    ``scientific-plotting`` skill's ``plot_utils_reference.py``). Layout
    is built from explicit fixed ``axes_rect`` rectangles so the plotting
    box has consistent physical size across re-runs — a key skill rule.

    Layout (canvas 18.0 × 11.5 cm, single deliberate canvas):
        Top   (0.55 height) : RIN spectra vs offset frequency, log-x.
        Mid   (0.16 height) : magnitude-squared coherence, log-x, 0..1.
        Bot   (0.16 height) : cross-spectrum phase, log-x, ±180°.
        Right (sidebar 24%) : self-contained annotation block (run /
                              acquisition / DC norm / RBW table / SSB).
    """
    from rp_rin_stream import _sciplot as sp  # vendored helpers
    import matplotlib.pyplot as plt

    f = result["f_Hz"]
    finite_f = f[np.isfinite(f) & (f > 0)]
    if finite_f.size == 0:
        raise ValueError("No positive-frequency points to plot.")
    f_lo, f_hi = float(finite_f.min()), float(finite_f.max())

    cfg = sp.resolve_cfg({
        "fig_width_cm": 18.0,
        "fig_height_cm": 11.5,
        "pad_inches": 0.04,
    })

    # Fixed plotting rectangles in figure coordinates (left, bottom, w, h).
    # Three stacked panels share the left 70 % of the canvas; the right
    # 26 % carries the annotation block. Hand-tuned for visual balance at
    # the canvas size above; do NOT change without re-checking labels.
    L = 0.080
    W = 0.620
    SIDE_L = 0.730
    SIDE_W = 0.260
    rect_rin = (L,    0.380, W, 0.585)   # top, tallest
    rect_coh = (L,    0.235, W, 0.140)
    rect_phs = (L,    0.085, W, 0.140)
    rect_side = (SIDE_L, 0.085, SIDE_W, 0.880)

    fig, ax_rin, cfg = sp.create_figure(
        cfg,
        size_cm=(cfg["fig_width_cm"], cfg["fig_height_cm"]),
        axes_rect=rect_rin,
    )
    ax_coh = sp.add_axes(fig, cfg, rect_coh)
    ax_phs = sp.add_axes(fig, cfg, rect_phs)
    ax_side = sp.add_axes(fig, cfg, rect_side)
    for spine in ax_side.spines.values():
        spine.set_visible(False)
    ax_side.tick_params(left=False, bottom=False,
                        labelleft=False, labelbottom=False)

    # Wong 2011 colour-blind safe palette
    c_ch1 = "#0072B2"   # blue
    c_ch2 = "#D55E00"   # vermillion
    c_xre = "#009E73"   # bluish-green
    c_xab = "#CC79A7"   # reddish-purple
    c_coh = "#000000"
    c_phs = "#6A5ACD"

    # ---- Top: RIN spectra (raw, no smoothing) ----
    ax_rin.set_xscale("log")
    ax_rin.plot(f, result["ch1_dBc_per_Hz"], color=c_ch1, linewidth=0.5,
                label=r"CH1 single-ch $S_{\mathrm{RIN},1}$")
    ax_rin.plot(f, result["ch2_dBc_per_Hz"], color=c_ch2, linewidth=0.5,
                label=r"CH2 single-ch $S_{\mathrm{RIN},2}$")
    if plot_cross_real:
        ax_rin.plot(f, result["cross_real_dBc_per_Hz"], color=c_xre,
                    linewidth=0.5, label=r"Re$\{S_{12}\}$")
    if plot_abs_cross:
        ax_rin.plot(f, result["cross_abs_dBc_per_Hz"], color=c_xab,
                    linewidth=0.5, linestyle=(0, (3, 2)),
                    label=r"$|S_{12}|$ (biased $\geq 0$)")
    if result["ssb_convention"] == "optical_rin":
        ylabel = "One-sided RIN (dBc / Hz)"
    else:
        ylabel = r"RF AM SSB $L(f)$ (dBc / Hz)"
    ax_rin.set_ylabel(ylabel)
    ax_rin.set_xlim(f_lo, f_hi)
    ax_rin.tick_params(labelbottom=False)
    # Faint band edges so the reader can see per-decade RBW changes.
    for s in result["summaries"]:
        edge = s["fhi"]
        if f_lo < edge < f_hi:
            ax_rin.axvline(edge, color="#bbbbbb", linewidth=0.3,
                           linestyle=":", zorder=0)
    sp.style_axes(ax_rin, cfg, grid=True)
    # Legend after style_axes — style_axes removes any pre-existing legend
    # when ``legend=None``/False (per the skill helper's behavior).
    ax_rin.legend(loc="upper right", handlelength=2.0, ncols=1)

    # ---- Middle: magnitude-squared coherence ----
    ax_coh.set_xscale("log")
    ax_coh.plot(f, result["coherence"], color=c_coh, linewidth=0.5)
    ax_coh.set_xlim(f_lo, f_hi)
    ax_coh.set_ylim(0.0, 1.0)
    ax_coh.set_ylabel(r"$\gamma^2_{12}$")
    ax_coh.tick_params(labelbottom=False)
    sp.style_axes(ax_coh, cfg, grid=True)

    # ---- Bottom: cross phase ----
    ax_phs.set_xscale("log")
    ax_phs.plot(f, result["cross_phase_deg"], color=c_phs,
                marker=".", markersize=0.8, linestyle="none")
    ax_phs.set_xlim(f_lo, f_hi)
    ax_phs.set_ylim(-185, 185)
    ax_phs.set_yticks([-180, -90, 0, 90, 180])
    ax_phs.set_ylabel(r"$\angle S_{12}$ (deg)")
    ax_phs.set_xlabel("Offset frequency (Hz)")
    ax_phs.axhline(0.0, color="#9aa3ac", linewidth=0.3, linestyle=":")
    sp.style_axes(ax_phs, cfg, grid=True)

    # ---- Sidebar: annotation block ----
    lines = []
    if meta is not None:
        if "run_dir" in meta:
            lines.append(r"$\bf{Run}$")
            lines.append(f"  {Path(meta['run_dir']).name}")
        lines.append(r"$\bf{Acquisition}$")
        if "fs_hz" in meta:
            lines.append(f"  $f_s = {meta['fs_hz']:.6g}$ Sa/s")
        if "decimation" in meta and meta["decimation"] > 0:
            lines.append(f"  decimation = {meta['decimation']}")
        if "duration_s_used" in meta:
            lines.append(f"  $T = {meta['duration_s_used']:.3f}$ s "
                         f"({meta['samples_used']:,} samp)")
        if meta.get("skip_samples", 0):
            lines.append(f"  skip = {meta['skip_samples']:,} samp "
                         f"({meta['skip_samples']/meta['fs_hz']:.3f} s)")
        if "full_scale_v" in meta:
            lines.append(f"  full-scale = $\\pm$ {meta['full_scale_v']:.2f} V")
        lines.append("")
    lines.append(r"$\bf{DC\ normalization}$")
    if dc1 is not None:
        lines.append(f"  $V_{{DC,1}} = {dc1:+.6g}$ V")
    if dc2 is not None:
        lines.append(f"  $V_{{DC,2}} = {dc2:+.6g}$ V")
    if ch1_stats is not None:
        lines.append(f"  CH1 mean = {ch1_stats['mean']*1e3:+.3f} mV")
        lines.append(f"  CH1 std  = {ch1_stats['std']*1e3:.4g} mV")
    if ch2_stats is not None:
        lines.append(f"  CH2 mean = {ch2_stats['mean']*1e3:+.3f} mV")
        lines.append(f"  CH2 std  = {ch2_stats['std']*1e3:.4g} mV")
    lines.append("")
    lines.append(r"$\bf{RBW\ /\ Hann\ ENBW}$")
    for s in result["summaries"]:
        # Format band as compact "1-10" / "10k-100k". Use the full ENBW
        # number but a shortened nperseg / N_avg representation so the
        # row fits inside the narrow sidebar column.
        lo, hi = s["flo"], s["fhi"]
        lo_s = f"{lo:g}" if lo < 1e3 else (f"{lo/1e3:g}k")
        hi_s = f"{hi:g}" if hi < 1e3 else (f"{hi/1e3:g}k")
        nseg = s["nperseg"]
        navg = s["n_avg"]
        nseg_s = f"{nseg/1e6:.2f}M" if nseg >= 1e6 else (
            f"{nseg/1e3:.0f}k" if nseg >= 1e4 else f"{nseg}"
        )
        lines.append(
            f"  {lo_s:>5}-{hi_s:<5} Hz: ENBW {s['actual_enbw']:g} Hz"
        )
        lines.append(f"          $N_{{seg}}{{=}}{nseg_s}, N_{{avg}}{{=}}{navg}$")
    lines.append("")
    lines.append(r"$\bf{SSB\ convention}$")
    lines.append(f"  {result['ssb_convention']}  (factor $={result['ssb_factor']:g}$)")

    ax_side.text(
        0.0, 1.0, "\n".join(lines),
        ha="left", va="top",
        fontsize=6.5, family="monospace",
        transform=ax_side.transAxes,
    )

    # Per scientific-plotting house rules, no figure title by default —
    # the caption carries the narrative. ``--plot-title`` still works.
    if suptitle:
        fig.suptitle(suptitle, fontsize=cfg["font_size"], y=0.99)

    out_pdf_path = Path(out_pdf) if out_pdf else None
    out_png_path = Path(out_png)
    sp.save_figure(fig, out_png_path, cfg)
    if out_pdf_path:
        sp.save_figure(fig, out_pdf_path, cfg)
    plt.close(fig)


def rin_cross_spectrum(args: argparse.Namespace | None = None) -> dict:
    """
    Run the full RIN cross-spectrum pipeline (load → normalize → compute →
    plot → save) and return the plotting data so external programs can
    consume it.

    All side effects (saving CSV/PNG/PDF, printing the input/RBW summaries
    and any warnings) match exactly what running this file as a script does.

    Parameters
    ----------
    args : argparse.Namespace, optional
        Parsed CLI arguments (see :func:`parse_args`). When ``None`` (the
        default), arguments are parsed from ``sys.argv`` via
        :func:`parse_args`. External callers passing a Namespace directly
        must populate **every** attribute that :func:`parse_args` would
        set (a partial Namespace will raise ``AttributeError`` somewhere
        downstream). The simplest way to obtain a full default-populated
        Namespace is to monkey-patch ``sys.argv`` to ``["prog"]`` and
        call :func:`parse_args`, then mutate only the fields you want to
        change before passing it in.

    Returns
    -------
    dict
        Keys:
            - "args": argparse.Namespace actually used (with any
              interactive ``input()`` resolutions applied).
            - "result": dict from :func:`compute_rin` containing the per-
              frequency spectrum arrays (``f_Hz``, ``S_rin_*``, dBc/Hz,
              coherence, cross phase, RBW/df/nperseg/n_avg, summaries,
              ``ssb_convention``, ``ssb_factor``).
            - "meta": dict | None — rpsa-run loader metadata (None when
              the input is a plain ``--file``).
            - "source_label": str describing the input source.
            - "fs_hz": float, sampling rate actually used.
            - "n_samples": int, samples per channel actually analyzed.
            - "dc1", "dc2": float, DC normalization voltages used.
            - "ch1_stats", "ch2_stats": ``{"mean": float, "std": float}``
              of the analyzed window.
            - "rbw_by_band_hz": OrderedDict, the post-``--rbw-scale`` band
              table actually fed to :func:`compute_rin`.
            - "ssb_convention": str.
            - "out_csv", "out_png", "out_pdf": str paths of written files.
    """
    if args is None:
        args = parse_args()

    meta = None
    if args.rpsa_run is not None:
        ch1, ch2, meta = load_rpsa_run(
            args.rpsa_run,
            skip_seconds=args.rpsa_skip_seconds,
            max_seconds=args.rpsa_max_seconds,
        )
        fs = meta["fs_hz"]
        if args.fs is not None and abs(args.fs - fs) / fs > 1e-9:
            print(f"Warning: --fs={args.fs:g} overridden by config.json fs={fs:g}.")
        source_label = f"rpsa-run {Path(args.rpsa_run).name}"
    else:
        if args.file is None:
            args.file = input("Input data file path (.npy/.npz/.csv/.txt) or --rpsa-run dir: ").strip()
        if args.fs is None:
            args.fs = float(input("Sampling rate fs in Sa/s, e.g. 1e6: ").strip())
        ch1, ch2 = load_two_channel_file(
            args.file, tuple(args.cols), args.skiprows, args.delimiter,
        )
        fs = float(args.fs)
        source_label = f"file {Path(args.file).name}"

    n = min(len(ch1), len(ch2))
    ch1 = np.asarray(ch1[:n], dtype=np.float64)
    ch2 = np.asarray(ch2[:n], dtype=np.float64)

    # DC normalization. For best accuracy, pass --dc1/--dc2 as laser-on mean minus dark offset.
    mean1 = float(np.mean(ch1))
    mean2 = float(np.mean(ch2))
    std1 = float(np.std(ch1))
    std2 = float(np.std(ch2))
    dc1 = float(args.dc1) if args.dc1 is not None else mean1 - float(args.dark1)
    dc2 = float(args.dc2) if args.dc2 is not None else mean2 - float(args.dark2)

    # Loud warning when DC is dominated by AC content (typical of dark-noise
    # captures where the user forgot to pass an explicit --dc1/--dc2).
    for ch_name, m, s, dc in [("CH1", mean1, std1, dc1), ("CH2", mean2, std2, dc2)]:
        if abs(dc) < 3.0 * s:
            print(
                f"WARNING: {ch_name} normalization DC ({dc:+.6g} V) is comparable to its std "
                f"({s:.6g} V). The resulting dBc/Hz numbers are likely meaningless. "
                "Pass --dc1/--dc2 set to the expected laser-on DC voltage."
            )

    # Apply --rbw-scale globally if the user wants more N_avg at the cost of
    # frequency resolution (or vice versa). This multiplies every band's
    # target ENBW; nperseg shrinks proportionally and N_avg grows ≈ linearly.
    import math
    rbw_scale = float(args.rbw_scale)
    if not math.isfinite(rbw_scale) or rbw_scale <= 0.0:
        raise SystemExit(
            f"--rbw-scale must be a finite positive number, got {args.rbw_scale!r}"
        )
    rbw_by_band = OrderedDict(
        (band, hz * rbw_scale) for band, hz in RBW_BY_BAND_HZ.items()
    )

    print("\nInput summary")
    print(f"  source              : {source_label}")
    print(f"  samples per channel : {n}")
    print(f"  fs                  : {fs:g} Sa/s")
    print(f"  record length       : {n / fs:.6g} s")
    print(f"  mean(ch1), mean(ch2): {mean1:.9g} V, {mean2:.9g} V")
    print(f"  std(ch1),  std(ch2) : {std1:.6g} V, {std2:.6g} V")
    print(f"  normalization dc1/2 : {dc1:.9g} V, {dc2:.9g} V")
    print(f"  SSB convention      : {args.ssb_convention}")
    print(f"  overlap, rbw_scale  : {args.overlap:.3g}, {rbw_scale:g}")

    result = compute_rin(
        ch1, ch2, fs, dc1, dc2,
        rbw_by_band_hz=rbw_by_band,
        overlap=args.overlap,
        detrend_constant=(not args.no_detrend),
        delay_sec=args.delay_sec,
        ssb_convention=args.ssb_convention,
    )

    print("\nRBW / averaging summary")
    for s in result["summaries"]:
        print(
            f"  {s['flo']:>8.3g} - {s['fhi']:<8.3g} Hz : "
            f"target RBW {s['target_rbw']:<8.4g} Hz, "
            f"actual ENBW {s['actual_enbw']:<8.4g} Hz, "
            f"df {s['df']:<8.4g} Hz, "
            f"nperseg {s['nperseg']:<9d}, averages {s['n_avg']:<6d}, bins {s['n_bins']}"
        )

    n_bad = int(np.sum(~np.isfinite(result["cross_real_dBc_per_Hz"])))
    if n_bad:
        print(
            f"\nNote: {n_bad} cross-real bins are <= 0 and are written as NaN in dBc/Hz. "
            "That usually means insufficient averaging, wrong phase/delay correction, "
            "or that the correlated RIN is below the residual uncorrelated floor. "
            "Inspect the cross_abs and phase/coherence columns as diagnostics."
        )

    out_csv = f"{args.out_prefix}.csv"
    out_png = f"{args.out_prefix}.png"
    out_pdf = f"{args.out_prefix}.pdf"
    save_csv(result, out_csv)
    # House style: no figure title unless user explicitly passes --plot-title.
    suptitle = args.plot_title
    make_plot(
        result, out_png,
        plot_abs_cross=args.plot_abs_cross,
        plot_cross_real=not args.no_plot_cross_real,
        meta=meta,
        dc1=dc1, dc2=dc2,
        ch1_stats={"mean": mean1, "std": std1},
        ch2_stats={"mean": mean2, "std": std2},
        suptitle=suptitle,
        out_pdf=out_pdf,
    )
    print(f"\nSaved: {out_csv}")
    print(f"Saved: {out_png}")
    print(f"Saved: {out_pdf}")

    return {
        "args": args,
        "result": result,
        "meta": meta,
        "source_label": source_label,
        "fs_hz": float(fs),
        "n_samples": int(n),
        "dc1": float(dc1),
        "dc2": float(dc2),
        "ch1_stats": {"mean": mean1, "std": std1},
        "ch2_stats": {"mean": mean2, "std": std2},
        "rbw_by_band_hz": rbw_by_band,
        "ssb_convention": args.ssb_convention,
        "out_csv": out_csv,
        "out_png": out_png,
        "out_pdf": out_pdf,
    }


def main():
    rin_cross_spectrum()


if __name__ == "__main__":
    main()
