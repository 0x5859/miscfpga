"""Independent verification of a known-frequency sine capture.

Four orthogonal tests for "is the data continuous":

  (T1) Cycle count via positive-going zero-crossings — must match
       f_actual × duration to within ≤ 1 cycle.

  (T2) Coherent inner product at FFT peak frequency over the whole
       record. A coherence ratio close to 1 rules out gross corruption
       (large phase jumps / large gaps / channel swaps). Note: this is a
       loose bound; the |cos(Φ/2)| derivation only applies to a single
       centered jump and is degraded by AWG drift / leakage / anti-alias
       rolloff. Treat T2 as a smoke test, not a quantitative bound.

  (T3) Per-pack-boundary phase-jump test using ONE-SIDED windows.
       For each boundary at sample b, we fit local phase twice:
         - Left window  [b-WIN, b]  → phase of pack k just before b
         - Right window [b, b+WIN]  → phase of pack k+1 just after b
       Both phases are referenced to time b/Fs, so for a continuous
       signal they agree to within fit noise (sub-degree). For a hidden
       gap of K samples between packs, the diff = wrap(2π f K / Fs) at
       FULL amplitude (not halved as a centered window would).
       Sensitivity at 100 kHz @ Fs=976562.5: 1-sample gap = 36.86°,
       10-sample gap = 8.64° (wrapped), 100-sample gap = 86.4° (wrapped).
       Blind spot: gaps where K f / Fs lands very close to an integer
       wrap to ~0° (e.g. K=39 ⇒ ~2.3°). T1 catches those because each
       integer cycle of phase change ≈ 9.77-sample gap counts as one
       missing cycle. **T1 + T3 together cover every K ≥ 1.**

  (T4) Pack-boundary vs interior sample first-difference distribution —
       if there is a hidden discontinuity at any pack boundary, that
       boundary's `x[k] - x[k-1]` value will be an outlier vs the
       distribution of interior diffs. This is independent of any
       frequency-domain assumption.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")
from rp_rin_stream.rpsa_reader import read_streams  # noqa: E402

RUN = Path("runs/20260504T090147Z_976562Sa_s")
F_NOMINAL = 100_000.0
PACK_SAMPLES = 131_072
ADC_HALF_SCALE = 8192


def main() -> int:
    cfg = json.loads((RUN / "config.json").read_text())
    fs = cfg["acquisition"]["effective_sample_rate_hz_per_channel"]
    full_scale = cfg["board"]["input_range_volts_peak_nominal"]
    print(f"Run: {RUN.name}")
    print(f"Fs = {fs} Sa/s/ch (nominal), full_scale = ±{full_scale} V\n")

    print("Loading BIN...")
    ch1_int, ch2_int = read_streams(RUN / "waveform.bin")
    n = ch1_int.size
    counts_to_v = full_scale / ADC_HALF_SCALE
    duration = n / fs
    expected_n = int(round(60.0 * fs))
    # Ceil division so that a truncated final pack (when -l limit lands
    # mid-pack) is still counted; T3 then includes the boundary into that
    # tail pack instead of silently skipping it.
    n_packs = math.ceil(n / PACK_SAMPLES)
    print(f"  N samples / channel: {n:,} ({'==' if n == expected_n else '!='} expected {expected_n:,})")
    print(f"  Duration:           : {duration} s")
    print(f"  Number of packs     : {n_packs} "
          f"({n // PACK_SAMPLES} full + {1 if n % PACK_SAMPLES else 0} truncated)")
    print()

    for label, x_int in [("CH1", ch1_int), ("CH2", ch2_int)]:
        x = x_int.astype(np.float64) * counts_to_v
        ac = x - x.mean()
        peak_amp = (x.max() - x.min()) / 2

        print(f"=========================== {label} ===========================")
        print(f"  mean={x.mean():+.5f} V  std={x.std():.5f} V  peak amp={peak_amp:.5f} V")

        # FFT peak (with quadratic sub-bin interpolation)
        spec = np.fft.rfft(ac)
        mag = np.abs(spec)
        peak_bin = int(mag.argmax())
        df = fs / n
        y0, y1, y2 = mag[peak_bin - 1], mag[peak_bin], mag[peak_bin + 1]
        denom = (y0 - 2 * y1 + y2)
        sub = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        f_actual = (peak_bin + sub) * df
        peak_amp_fft = 2 * mag[peak_bin] / n
        side_lo = mag[peak_bin - 1]
        side_hi = mag[peak_bin + 1]
        del spec, mag

        print(f"  Signal frequency derived from FFT peak: {f_actual:.6f} Hz "
              f"(Δ vs {F_NOMINAL/1000:.0f} kHz = {(f_actual - F_NOMINAL)*1e3:+.3f} mHz, "
              f"≈ {(f_actual - F_NOMINAL) / F_NOMINAL * 1e6:+.2f} ppm)")

        # T1: cycle count
        s = np.sign(ac)
        nz = np.where(s != 0, s, 1)
        crossings = int(((nz[:-1] < 0) & (nz[1:] >= 0)).sum())
        expected_cyc = f_actual * duration
        print(f"\n  [T1] Cycle count via pos-going zero crossings: {crossings:,}")
        print(f"       Expected (f_actual × duration):           {expected_cyc:,.4f}")
        print(f"       Discrepancy:                               {crossings - expected_cyc:+.4f} cycles")
        # Tightened from ±2 to ±1: ±2 would tolerate ~19 lost samples
        # (1 cycle ≈ 9.77 samples at 100 kHz / 976562.5 Sa/s).
        print(f"       Verdict: {'PASS' if abs(crossings - expected_cyc) <= 1 else 'FAIL'}")

        # T2: coherent inner product over WHOLE record at f_actual
        idx = np.arange(n, dtype=np.float64)
        c = np.cos(2 * np.pi * f_actual * idx / fs)
        s_ = np.sin(2 * np.pi * f_actual * idx / fs)
        I = (ac * c).mean()
        Q = (ac * s_).mean()
        coh_amp = 2 * np.hypot(I, Q)
        ratio = coh_amp / peak_amp
        # If a single mid-record phase jump of angle Φ existed:
        #   ratio ≈ |cos(Φ/2)|  → angle Φ ≈ 2 arccos(ratio)
        # Solve for the worst-case implied phase jump.
        if ratio <= 1.0:
            implied_jump_deg = float(np.degrees(2 * np.arccos(ratio)))
        else:
            implied_jump_deg = 0.0
        print(f"\n  [T2] Coherent inner product over whole record at f_actual:")
        print(f"       coherent amp                 : {coh_amp:.5f} V")
        print(f"       p-p / 2 (raw peak amplitude) : {peak_amp:.5f} V")
        print(f"       FFT-peak-bin amp             : {peak_amp_fft:.5f} V")
        print(f"       coherence ratio (coh/peak)   : {ratio:.4f}")
        # Caveat: the |cos(Φ/2)| bound applies only to a single centered
        # phase jump on an otherwise-perfect sine.  Off-center jumps,
        # multiple smaller jumps, AWG amplitude drift, near-coherent
        # leakage, and anti-alias rolloff all degrade the ratio without
        # being continuity defects.  So treat this as a smoke test.
        print(f"       (single-centered-jump bound: ≤ {implied_jump_deg:.2f}°; loose due to AWG/clock drift)")
        print(f"       Verdict: {'PASS' if ratio > 0.85 else 'FAIL'}  (>0.85 ⇒ rules out gross corruption)")

        # T3: per-pack-boundary phase-jump test using ONE-SIDED windows.
        # The previous version centered the window on the boundary, which
        # halved the registered amplitude of any real gap (the fit
        # averaged pre-gap and post-gap phases). Here we use two
        # uncontaminated windows (one entirely in pack k, one entirely
        # in pack k+1) so a K-sample gap produces FULL `2π f K / Fs`.
        WIN = 4096

        def phase_at(window_lo: int, window_hi: int, ref_sample: int) -> float:
            """Estimate signal phase at `ref_sample` using samples in the window.

            The reference sinusoid is shifted to `ref_sample`, so a
            window entirely on one side of the boundary still extrapolates
            to the same phase reference as a window on the other side —
            unless there's a discontinuity between the two windows.
            """
            lo, hi = max(0, window_lo), min(n, window_hi)
            i_loc = np.arange(lo, hi, dtype=np.float64)
            seg = ac[lo:hi] - ac[lo:hi].mean()
            rel = (i_loc - ref_sample) / fs
            cc = np.cos(2 * np.pi * f_actual * rel)
            ss = np.sin(2 * np.pi * f_actual * rel)
            return float(np.arctan2((seg * ss).sum(), (seg * cc).sum()))

        boundaries = list(range(PACK_SAMPLES, n_packs * PACK_SAMPLES, PACK_SAMPLES))
        # For each boundary, compare phase from the LEFT window
        # [b-WIN, b] vs phase from the RIGHT window [b, b+WIN].
        jumps = []
        for b in boundaries:
            phi_left = phase_at(b - WIN, b, b)
            phi_right = phase_at(b, b + WIN, b)
            jump = (phi_right - phi_left + np.pi) % (2 * np.pi) - np.pi
            jumps.append(jump)
        jumps = np.asarray(jumps)
        max_jump_deg = float(np.degrees(np.abs(jumps).max()))
        rms_jump_deg = float(np.degrees(np.sqrt((jumps ** 2).mean())))
        bad = int((np.abs(jumps) > np.radians(5.0)).sum())
        # Sensitivity references at f_actual:
        single_sample_excursion_deg = 360.0 * f_actual / fs       # K=1 full
        ten_sample_excursion_deg = float(
            np.degrees(((2 * np.pi * f_actual * 10 / fs + np.pi) % (2 * np.pi)) - np.pi)
        )
        print(f"\n  [T3] Per-boundary phase jump (left-window vs right-window, "
              f"N={len(jumps)} boundaries):")
        print(f"       max |jump|: {max_jump_deg:.4f}°")
        print(f"       rms |jump|: {rms_jump_deg:.4f}°")
        print(f"       Sensitivity references at f_actual = {f_actual:.3f} Hz:")
        print(f"         1-sample gap   → {single_sample_excursion_deg:.2f}°  "
              f"(SNR vs rms: ~{single_sample_excursion_deg / max(rms_jump_deg, 1e-9):.0f}×)")
        print(f"         10-sample gap  → {abs(ten_sample_excursion_deg):.2f}° (wrapped)")
        print(f"       boundaries with |jump| > 5°: {bad}  "
              f"(any > 0 would suggest a gap)")
        print(f"       Verdict: {'PASS' if bad == 0 else 'FAIL'}")

        # T4: pack-boundary first-difference vs interior first-difference
        # If a hidden gap injects a sample-level discontinuity at any
        # boundary, the boundary's `x[k]-x[k-1]` will be an outlier vs
        # the distribution of interior diffs. Independent of any
        # frequency-domain assumption.
        boundary_diffs = np.array([
            x[b] - x[b - 1] for b in boundaries
        ])
        # Sample interior diffs as a baseline (every PACK_SAMPLES//2
        # within-pack diffs to match boundary sample size).
        rng = np.random.default_rng(0)
        interior_idx = rng.integers(low=2, high=n - 1, size=max(len(boundary_diffs) * 8, 4096))
        interior_diffs = x[interior_idx] - x[interior_idx - 1]
        b_max = float(np.abs(boundary_diffs).max())
        i_max = float(np.abs(interior_diffs).max())
        b_std = float(boundary_diffs.std())
        i_std = float(interior_diffs.std())
        b_outliers = int((np.abs(boundary_diffs) > 4 * i_std).sum())
        print(f"\n  [T4] Pack-boundary 1st-difference vs interior baseline:")
        print(f"       boundary diffs: N={len(boundary_diffs)}, "
              f"max |x[b]-x[b-1]|={b_max:.4f} V, std={b_std:.4f} V")
        print(f"       interior diffs: N={len(interior_diffs)}, "
              f"max={i_max:.4f} V, std={i_std:.4f} V")
        print(f"       boundary 4σ-outliers vs interior σ: {b_outliers}  "
              f"(any > 0 would suggest a gap)")
        print(f"       Verdict: {'PASS' if b_outliers == 0 else 'FAIL'}")
        print()

    print("All four orthogonal tests should be PASS for both channels for the")
    print("data to be considered continuous and at the expected sample rate.")
    print()

    # ---- Self-test: inject synthetic gaps and confirm T3 catches them ----
    # This proves the script can actually detect what it claims to detect,
    # given the present noise floor.
    print("=========================== T3 sensitivity self-test ===========================")
    rng = np.random.default_rng(0)
    # Use CH2 (higher SNR signal). Pick a deep-interior pack boundary.
    x = ch2_int.astype(np.float64) * counts_to_v
    ac = x - x.mean()
    f_act = 100000.533125            # close enough; same value the test would use
    test_b = 224 * PACK_SAMPLES      # mid-record pack boundary
    WIN = 4096

    def phase_using_data_at_indices(data: np.ndarray, indices: np.ndarray,
                                     ref: int) -> float:
        """Estimate signal phase at `ref`, given data values placed at the
        given source-time indices. The reference sinusoid is built from
        ``indices`` (NOT from data position), so swapping ``data`` to a
        time-shifted copy lets us simulate the data being from later in
        the source stream while pretending it sits at this boundary —
        which is exactly what a hidden K-sample gap would look like.
        """
        seg = data - data.mean()
        rel = (indices.astype(np.float64) - ref) / fs
        cc = np.cos(2 * np.pi * f_act * rel)
        ss = np.sin(2 * np.pi * f_act * rel)
        return float(np.arctan2((seg * ss).sum(), (seg * cc).sum()))

    # Baseline jump at this boundary in the real (unmodified) data
    left_idx = np.arange(test_b - WIN, test_b)
    right_idx = np.arange(test_b, test_b + WIN)
    pl = phase_using_data_at_indices(ac[left_idx[0]:left_idx[-1] + 1], left_idx, test_b)
    pr = phase_using_data_at_indices(ac[right_idx[0]:right_idx[-1] + 1], right_idx, test_b)
    base_jump_deg = float(np.degrees(((pr - pl + np.pi) % (2 * np.pi)) - np.pi))
    print(f"  baseline jump at sample idx {test_b}: {base_jump_deg:+.4f}° (real data)")

    for k in [1, 5, 10, 39, 100, 1000]:
        if test_b + WIN + k >= ac.size:
            continue
        # Inject a K-sample gap: the right window is fed data from
        # `[b+k, b+WIN+k)` (post-gap source samples) but the reference
        # sinusoid is computed at indices `[b, b+WIN)` — so the I/Q
        # correlation reads the post-gap signal as if it were at the
        # boundary, exactly as a real gap would.
        right_data_shifted = ac[test_b + k:test_b + WIN + k]
        pl_inj = phase_using_data_at_indices(ac[test_b - WIN:test_b], left_idx, test_b)
        pr_inj = phase_using_data_at_indices(right_data_shifted, right_idx, test_b)
        jump_deg = float(np.degrees(((pr_inj - pl_inj + np.pi) % (2 * np.pi)) - np.pi))
        expected_full = (360.0 * f_act * k / fs) % 360.0
        if expected_full > 180:
            expected_full -= 360
        verdict = "PASS — caught" if abs(jump_deg) > 5 else "MISS — needs T1 to catch"
        print(f"  K={k:>4d} samples: registered jump = {jump_deg:+8.3f}°  "
              f"(expected wrapped ≈ {expected_full:+7.3f}°, detection: {verdict})")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
