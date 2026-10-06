#!/usr/bin/env python3
"""Post-run analysis of a fixed pilot run.

Adds, on top of the whole-frame statistics already in run_manifest.json:

  * a stratified view by Mel floor activity and by frame level.  This is an
    ADDITIONAL cut, not a replacement: the overall numbers stay primary and no
    frame is excluded from them.
  * where residual error is observed, without attributing it to one FFT
    quantization boundary before an ablation study.
  * the frame-overlap count for the reused FFT's continuous output, used to
    correct the earlier FIFO depth claim in docs/reviews/FFT_REUSE_REVIEW.md.

Usage:  python verification/fixed/analyze_pilot.py <run_dir>
"""

from __future__ import annotations

import json
import math
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

REF = Path(r"D:\2610_MFCC\build\python_reference\reproduce_01")
DEV = "8463-294828-0037"
PROFILE = "comparison_raw13"


def mel_floor_mask(mel_i: np.ndarray, shift: np.ndarray, fw: int,
                   floor: float) -> np.ndarray:
    """Compare exact Q1 pilot energies, T*2^-(19+2s+Fw), with the floor.

    These saved pilot arrays use the preserved 16/F15 FFT with physical S=9.
    Positive T can still fall below the floor; zero-count is a separate metric.
    """
    if mel_i.ndim != 2 or shift.shape != (mel_i.shape[0],):
        raise ValueError("Mel/shift array shapes do not match")
    if not math.isfinite(floor) or floor <= 0:
        raise ValueError("floor must be positive and finite")
    threshold = Fraction(floor)
    result = np.zeros(mel_i.shape, dtype=bool)
    for t, s in enumerate(shift):
        exponent = -(19 + 2 * int(s) + fw)
        scale = Fraction(2) ** exponent
        for m, value in enumerate(mel_i[t]):
            value = int(value)
            if value < 0:
                raise ValueError("negative Mel energy is invalid, not floor")
            result[t, m] = value * scale < threshold
    return result


def main(run_dir: Path) -> int:
    man = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    cands = list(man["candidates"].keys())
    fw = int(man["mel_table"]["frac_bits_Fw"])
    floor = float(man["log_floor"])

    ref_dir = REF / "development" / DEV / PROFILE
    ref_mfcc = np.fromfile(ref_dir / "mfcc.bin", dtype="<f8").reshape(-1, 13)
    ref_logmel = np.fromfile(ref_dir / "log_mel.bin", dtype="<f8").reshape(-1, 26)
    ref_mel = np.fromfile(ref_dir / "mel_energies.bin",
                          dtype="<f8").reshape(-1, 26)
    windowed = np.fromfile(ref_dir / "windowed.bin",
                           dtype="<f8").reshape(-1, 512)
    peak = np.max(np.abs(windowed), axis=1)
    nframes = ref_mfcc.shape[0]

    print("=" * 96)
    print("A. Development clip: whole-frame statistics stay primary")
    print("=" * 96)
    print(f"  frames = {nframes}; windowed peak |u| min/median/max = "
          f"{peak.min():.3e} / {np.median(peak):.3e} / {peak.max():.3e}")
    print(f"  {'candidate':18s} {'mfcc max':>11s} {'mfcc rmse':>11s} "
          f"{'logmel max':>11s} {'logmel rmse':>12s} {'floor hits':>11s}")
    data = {}
    for c in cands:
        mf = np.fromfile(run_dir / "arrays" / f"{DEV}_{c}_mfcc_f64.bin",
                         dtype="<f8").reshape(-1, 13)
        mel_i = np.fromfile(run_dir / "arrays" / f"{DEV}_{c}_mel_i64.bin",
                            dtype="<i8").reshape(-1, 26)
        shift = np.fromfile(run_dir / "arrays" / f"{DEV}_{c}_shift_i64.bin",
                            dtype="<i8")
        d = np.abs(mf - ref_mfcc)
        s = next(r for r in man["results"] if r["id"] == DEV)["candidates"][c]
        floor_mask = mel_floor_mask(mel_i, shift, fw, floor)
        if int(floor_mask.sum()) != s["mel_floor_hit_count"]:
            raise ValueError(f"{c}: recomputed floor count differs from manifest")
        data[c] = {"mfcc": mf, "mel_i": mel_i, "shift": shift, "d": d,
                   "summary": s, "floor_mask": floor_mask}
        print(f"  {c:18s} {d.max():11.4e} "
              f"{np.sqrt(np.mean(d ** 2)):11.4e} "
              f"{s['stages']['log_mel']['max_abs']:11.4e} "
              f"{s['stages']['log_mel']['rmse']:12.4e} "
              f"{s['mel_floor_hit_count']:11d}")

    print()
    print("=" * 96)
    print("B. Stratified by Mel floor activity (additional cut; no frame is")
    print("   dropped from the statistics in section A)")
    print("=" * 96)
    for c in cands:
        mel_i = data[c]["mel_i"]
        d = data[c]["d"]
        floor_mask = data[c]["floor_mask"]
        hit = np.any(floor_mask, axis=1)
        print(f"  {c}")
        print(f"    integer-zero cells={int((mel_i == 0).sum())}; "
              f"floor cells={int(floor_mask.sum())}; "
              f"positive-integer floor cells={int((floor_mask & (mel_i > 0)).sum())}; "
              f"candidate-only floor cells={int((floor_mask & (ref_mel > floor)).sum())}")
        for label, mask in (("frames with a floored Mel band", hit),
                            ("frames with none", ~hit)):
            if not mask.any():
                print(f"    {label:30s} count=0")
                continue
            print(f"    {label:30s} count={int(mask.sum()):4d}  "
                  f"mfcc max={d[mask].max():.4e}  "
                  f"rmse={np.sqrt(np.mean(d[mask] ** 2)):.4e}")

    print()
    print("=" * 96)
    print("C. Stratified by frame level (additional cut only)")
    print("=" * 96)
    bands = [(0.0, 1e-3), (1e-3, 1e-2), (1e-2, 1e-1), (1e-1, 1.0)]
    for c in cands:
        d = data[c]["d"]
        mel_i = data[c]["mel_i"]
        print(f"  {c}")
        print(f"    {'peak range':>18s} {'frames':>7s} {'mfcc max':>11s} "
              f"{'mfcc rmse':>11s} {'zero mel bands':>15s}")
        for lo, hi in bands:
            m = (peak >= lo) & (peak < hi)
            if not m.any():
                continue
            print(f"    [{lo:8.1e},{hi:8.1e}) {int(m.sum()):7d} "
                  f"{d[m].max():11.4e} {np.sqrt(np.mean(d[m] ** 2)):11.4e} "
                  f"{int((mel_i[m] == 0).sum()):15d}")

    print()
    print("=" * 96)
    print("D. Where the residual error sits for the BFP pilot candidate")
    print("=" * 96)
    best = "bfp_pre_quant" if "bfp_pre_quant" in cands else cands[0]
    mel_i = data[best]["mel_i"]
    zero_mask = mel_i == 0
    band_counts = zero_mask.sum(axis=0)
    print(f"  candidate: {best}")
    print(f"  zero Mel integers per band index:")
    print("   ", np.array2string(band_counts, max_line_width=92))
    nz = np.nonzero(band_counts)[0]
    print(f"  bands that ever hit zero: {nz.tolist()}")
    if nz.size:
        print(f"  reference energy in those (band, frame) cells: "
              f"min={ref_mel[zero_mask].min():.3e} "
              f"max={ref_mel[zero_mask].max():.3e}")
        print(f"  reference ln of those cells: "
              f"min={ref_logmel[zero_mask].min():.3f} "
              f"max={ref_logmel[zero_mask].max():.3f}  "
              f"(the floor substitutes ln(1e-12) = {np.log(1e-12):.3f})")
    sh = data[best]["shift"]
    print(f"  chosen exponent s: min={sh.min()} max={sh.max()} "
          f"mean={sh.mean():.2f}")
    print("  Note: these zero cells locate energy loss but do not isolate its "
          "cause.\n  Input quantization, internal FFT rounding/twiddles, and "
          "the final output grid\n  must be compared by ablation. The log here "
          "is float64; integer log cannot\n  recover energy already lost. See "
          "FIXED_POINT_PRECISION_REVIEW.md for the\n  precision study; RMSE "
          "differences are not independent additive contributions.")

    print()
    print("=" * 96)
    print("E. Reused FFT: how many frames overlap in the continuous output")
    print("   (corrects the FIFO depth claim in FFT_REUSE_REVIEW.md)")
    print("=" * 96)
    # Measured from the recorded continuous run: gapless input, N=512,
    # first-output latency 1048 cycles, output also gapless.
    N, LAT = 512, 1048
    span = LAT + N - 1            # first accept -> last beat of that frame
    worst, worst_u = 0, None
    for u in range(0, 6 * N):
        lo = max(0, -(-(u - span) // N))      # ceil((u-span)/N)
        hi = u // N
        n = hi - lo + 1
        if n > worst:
            worst, worst_u = n, u
    print(f"  N={N}, measured first-output latency={LAT} cycles, "
          f"frame span={span} cycles")
    print(f"  maximum number of frames accepted but not yet fully output: "
          f"{worst}  (at cycle offset {worst_u})")
    print(f"  floor((latency + N - 1)/N) + 1 = "
          f"{span // N + 1}")
    print("  => the earlier 'at most 3 frames / depth 3N is enough' statement "
          "is wrong.\n     Depth and credit scheme must be settled by the "
          "follow-up protocol design,\n     not by this count alone.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
