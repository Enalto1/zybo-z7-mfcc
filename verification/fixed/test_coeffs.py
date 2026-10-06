#!/usr/bin/env python3
"""Checks of the Mel filterbank generator and its quantization.

  1. The regenerated float64 filterbank is compared bit-for-bit against the
     frozen reference table (read only).  If the frozen run is not present the
     check is reported as SKIPPED rather than silently passing.
  2. Hand-derived properties of the table (peak one, edge count, triangle
     support) are checked.
  3. Weight quantization is checked at exact ties and at the peak, and the
     declared unsigned width is checked against the largest weight.
  4. The coefficient-only error is measured for several Fw, so Fw = 16 is
     visibly a candidate rather than a confirmed value.

Run:  python verification/fixed/test_coeffs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from software.fixed_model.coeffs import (  # noqa: E402
    coefficient_error_sweep, hz_to_mel, mel_filterbank_float, mel_to_hz,
    quantize_mel_filterbank)

FROZEN = Path(r"D:\2610_MFCC\build\python_reference\reproduce_01"
              r"\coefficients\comparison_raw13")

FAILS: list[str] = []
SKIPS: list[str] = []
CHECKS = 0


def eq(label, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{label}: got {got!r}, expected {want!r}")


def le(label, got, limit):
    global CHECKS
    CHECKS += 1
    if not (got <= limit):
        FAILS.append(f"{label}: got {got!r}, expected <= {limit!r}")


# --------------------------------------------------- mel scale round trip
eq("hz_to_mel(0)", hz_to_mel(0.0), 0.0)
le("mel round trip 1000 Hz", abs(mel_to_hz(hz_to_mel(1000.0)) - 1000.0), 1e-9)
le("mel round trip 8000 Hz", abs(mel_to_hz(hz_to_mel(8000.0)) - 8000.0), 1e-8)

# --------------------------------------------------- structure
fb, edges = mel_filterbank_float()
arr = np.asarray(fb)
eq("shape", arr.shape, (26, 257))
eq("edge count", len(edges), 28)
eq("edges non-decreasing", all(edges[i] <= edges[i + 1]
                               for i in range(len(edges) - 1)), True)
eq("first edge", edges[0], 0)
eq("last edge", edges[-1], 256)
eq("min weight", float(arr.min()), 0.0)
eq("max weight is exactly one", float(arr.max()), 1.0)
eq("every band peaks at one",
   bool(np.all(np.isclose(arr.max(axis=1), 1.0))), True)
# A band's support lies inside [left, right) and peaks at the centre edge.
# The weight at k == left is (left-left)/(centre-left) == 0 by definition, so
# the first NON-ZERO bin is left+1 whenever centre > left.
for m in range(26):
    nz = np.nonzero(arr[m])[0]
    left, centre, right = edges[m], edges[m + 1], edges[m + 2]
    eq(f"band {m} first nonzero", int(nz[0]),
       left + 1 if centre > left else centre)
    le(f"band {m} last nonzero", int(nz[-1]), right - 1)
    eq(f"band {m} peak at centre edge", int(np.argmax(arr[m])), centre)
    eq(f"band {m} zero below left", bool(np.all(arr[m][:left + 1] == 0.0)
                                        or centre == left), True)
    eq(f"band {m} zero at and above right",
       bool(np.all(arr[m][right:] == 0.0)), True)

# --------------------------------------------------- vs frozen reference
if (FROZEN / "mel_filters.bin").is_file():
    frozen = np.fromfile(FROZEN / "mel_filters.bin", dtype="<f8").reshape(26, 257)
    frozen_edges = np.fromfile(FROZEN / "mel_edges.bin", dtype="<i8").tolist()
    eq("frozen edges identical", edges, frozen_edges)
    eq("frozen filterbank bit-identical", bool(np.array_equal(arr, frozen)), True)
else:
    SKIPS.append(f"frozen reference table not found at {FROZEN}")

# --------------------------------------------------- weight quantization
t16 = quantize_mel_filterbank(16)
eq("Fw16 width unsigned", t16.weight_width_unsigned, 17)
eq("Fw16 peak weight is 2**16", t16.max_weight_int, 65536)
eq("Fw16 peak needs 17 bits", 65536 > 2 ** 16 - 1, True)
le("Fw16 max coefficient error <= half LSB",
   t16.max_abs_coefficient_error, 0.5 / 2 ** 16 + 1e-18)
eq("Fw16 worst band sum", t16.per_band_weight_sum_int[25], 1540096)
eq("Fw16 worst band sum equals 23.5 * 2**16",
   t16.per_band_weight_sum_int[25], int(23.5 * 65536))
eq("nonzero bin counts", t16.per_band_nonzero_bins[:3], [3, 4, 5])

# exact-tie behaviour of the weight quantizer: a weight of exactly k+0.5 LSB
# must go to even.  Band 1 has weights with denominator 2 (edges differ by 2),
# so 0.5 at Fw=1 is a real tie: 0.5*2 = 1.0, not a tie; use Fw=0 instead where
# 0.5*1 = 0.5 is a tie -> 0.
t0 = quantize_mel_filterbank(0)
half_positions = [(m, k) for m in range(26) for k in range(257)
                  if t0.weights_float[m][k] == 0.5]
eq("found exact 0.5 weights", len(half_positions) > 0, True)
eq("exact 0.5 at Fw=0 rounds to even (0)",
   {t0.weights_int[m][k] for m, k in half_positions}, {0})
eq("weight 1.0 at Fw=0 stays 1", t0.max_weight_int, 1)

# accumulator bounds
b = t16.accumulator_bound(2 * 32768 * 32768)
eq("tight bound bits", b["tight_bound_bits_unsigned"], 52)
eq("loose bound bits", b["loose_bound_bits_unsigned"], 56)
eq("worst band index", b["worst_band_index"], 25)
eq("tight bound is below loose bound",
   b["tight_bound_from_this_table"] < b["loose_bound_all_bins_weight_one"], True)

# manifest is self-describing and marks Fw as a candidate
man = t16.manifest()
eq("manifest marks candidate", man["status"].startswith("candidate"), True)
eq("manifest records Fw", man["frac_bits_Fw"], 16)
eq("manifest has a weight hash", len(man["weights_int_sha256"]), 64)

# --------------------------------------------------- Fw sweep on real power
if (FROZEN.parent.parent / "development" / "8463-294828-0037"
        / "comparison_raw13" / "power.bin").is_file():
    p = np.fromfile(FROZEN.parent.parent / "development" / "8463-294828-0037"
                    / "comparison_raw13" / "power.bin",
                    dtype="<f8").reshape(-1, 257)
    rows = coefficient_error_sweep(p[:64])
    print("  Mel coefficient-only error (development power, 64 frames):")
    print(f"    {'Fw':>3} {'width':>5} {'max|coef err|':>14} "
          f"{'max|dE|':>12} {'max rel dE':>11} {'max|dlnE| nats':>15} {'acc bits':>9}")
    for r in rows:
        print(f"    {r['frac_bits_Fw']:3d} {r['weight_width_unsigned']:5d} "
              f"{r['max_abs_coefficient_error']:14.3e} "
              f"{r['mel_energy_max_abs_error']:12.3e} "
              f"{r['mel_energy_max_rel_error']:11.3e} "
              f"{r['log_mel_max_abs_error_nats']:15.3e} "
              f"{r['accumulator_bits_tight']:9d}")
    byfw = {r["frac_bits_Fw"]: r for r in rows}
    eq("coefficient error shrinks with Fw",
       byfw[20]["max_abs_coefficient_error"] <
       byfw[12]["max_abs_coefficient_error"], True)
    eq("log error shrinks with Fw",
       byfw[20]["log_mel_max_abs_error_nats"] <=
       byfw[12]["log_mel_max_abs_error_nats"], True)
else:
    SKIPS.append("frozen development power.bin not found; Fw sweep skipped")

print(f"test_coeffs: {CHECKS} checks, {len(FAILS)} failures, "
      f"{len(SKIPS)} skipped")
for s in SKIPS:
    print("  SKIP", s)
for f in FAILS:
    print("  FAIL", f)
print("RESULT:", "PASS" if not FAILS else "FAIL")
sys.exit(0 if not FAILS else 1)
