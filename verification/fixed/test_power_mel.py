#!/usr/bin/env python3
"""Checks of the integer Power/Mel stage and the scale algebra.

Three things are checked independently of the code under test:
  1. psum widths and exactness at the signed-16 boundaries.
  2. The exponent algebra, derived here by hand and compared both to
     power_mel.py and to the numbers printed in FIXED_POINT_DEVELOPMENT.md.
  3. The log floor on a BFP path: a strictly positive integer energy can be
     below the common 1e-12 floor once the frame exponent is applied, so
     "T == 0" is not a valid floor test.

Run:  python verification/fixed/test_power_mel.py
"""

from __future__ import annotations

import math
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.fixed_model.coeffs import quantize_mel_filterbank  # noqa: E402
from software.fixed_model.power_mel import (  # noqa: E402
    PSUM_WIDTH_UNSIGNED, PowerMelScales, check_scale_derivation,
    log_mel_with_floor, mel_accumulate, mel_exponent, power_exponent,
    power_integer)
from software.fixed_model.qnum import OverflowCounter  # noqa: E402

FAILS: list[str] = []
CHECKS = 0


def eq(label, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{label}: got {got!r}, expected {want!r}")


def close(label, got, want, tol):
    global CHECKS
    CHECKS += 1
    if not (abs(got - want) <= tol):
        FAILS.append(f"{label}: got {got!r}, expected {want!r} +-{tol}")


def raises(label, exc, fn, *a, **kw):
    global CHECKS
    CHECKS += 1
    try:
        fn(*a, **kw)
    except exc:
        return
    FAILS.append(f"{label}: did not raise {exc.__name__}")


# --------------------------------------------------- psum width and exactness
eq("psum width", PSUM_WIDTH_UNSIGNED, 32)
# 32767**2 * 2 = 2147352578 ; -32768**2 * 2 = 2147483648 = 2**31
eq("psum max positive", power_integer([32767], [32767])[0], 2147352578)
eq("psum min-negative pair", power_integer([-32768], [-32768])[0], 2 ** 31)
eq("psum 2**31 fits unsigned 32", 2 ** 31 <= 2 ** 32 - 1, True)
eq("psum zero", power_integer([0], [0])[0], 0)
eq("psum real only", power_integer([100], [0])[0], 10000)
eq("psum sign independent", power_integer([-7], [24])[0], 49 + 576)
# anything wider than the signed-16 FFT output contract must be reported
raises("psum too wide", OverflowError, power_integer, [2 ** 20], [2 ** 20])

# --------------------------------------------------- exponent algebra by hand
# P = psum * 2**(2S + 2 - 2s - 30 - log2 N).  N = 512, S = 9:
#   2*9 + 2 - 0 - 30 - 9 = -19
eq("power_exp N512 S9 s0", power_exponent(512, 9, 0), -19)
eq("power_exp N512 S9 s1", power_exponent(512, 9, 1), -21)
eq("power_exp N512 S9 s5", power_exponent(512, 9, 5), -29)
eq("power_exp N512 S9 s-1", power_exponent(512, 9, -1), -17)
eq("power_exp matches -(19+2s) s=7", power_exponent(512, 9, 7), -(19 + 14))
# a different size, to show the formula is not hard-coded for 512:
# N = 1024, S = 10: 2*10 + 2 - 0 - 30 - 10 = -18
eq("power_exp N1024 S10 s0", power_exponent(1024, 10, 0), -18)
eq("mel_exp N512 S9 s0 Fw16", mel_exponent(512, 9, 0, 16), -35)
eq("mel_exp N512 S9 s10 Fw16", mel_exponent(512, 9, 10, 16), -55)
raises("power_exp non power of two", ValueError, power_exponent, 500, 9, 0)

d = check_scale_derivation(512, 9, 0, 16)
eq("derivation agrees with doc (power)", d["power_exp_matches_doc"], True)
eq("derivation agrees with doc (mel)", d["mel_exp_matches_doc"], True)
for s in (-2, -1, 0, 1, 3, 9, 17, 24):
    dd = check_scale_derivation(512, 9, s, 16)
    eq(f"derivation s={s}", (dd["power_exp_matches_doc"],
                             dd["mel_exp_matches_doc"]), (True, True))

sc = PowerMelScales(nfft=512, total_fft_shift_S=9, bfp_shift_s=3,
                    mel_frac_bits_Fw=16)
eq("scales power", sc.power_exp_log2, -25)
eq("scales mel", sc.mel_exp_log2, -41)

# --------------------------------------------------- mel accumulate exactness
table = quantize_mel_filterbank(16)
eq("mel table width", table.weight_width_unsigned, 17)
eq("mel table max weight", table.max_weight_int, 65536)
psum = [0] * table.num_bins
psum[3] = 1000
psum[4] = 2000
expected = [1000 * table.weights_int[m][3] + 2000 * table.weights_int[m][4]
            for m in range(table.num_filters)]
eq("mel accumulate exact", mel_accumulate(psum, table), expected)
eq("mel accumulate all zero", mel_accumulate([0] * table.num_bins, table),
   [0] * table.num_filters)

# worst case must fit the declared accumulator width
bound = table.accumulator_bound(2 ** 31)
full = [2 ** 31] * table.num_bins
c = OverflowCounter()
got = mel_accumulate(full, table, bound["tight_bound_bits_unsigned"], c)
eq("worst case fits tight bound", c.total(), 0)
eq("worst case equals bound for worst band",
   max(got), bound["tight_bound_from_this_table"])
eq("tight bound is 52 bits at Fw=16", bound["tight_bound_bits_unsigned"], 52)
eq("loose 257-bin bound is 56 bits", bound["loose_bound_bits_unsigned"], 56)
# an accumulator one bit short of the tight bound must be reported
raises("accumulator too narrow", OverflowError, mel_accumulate, full, table,
       bound["tight_bound_bits_unsigned"] - 1)

# --------------------------------------------------- log floor with BFP exponent
LOG_FLOOR = 1e-12
ln_floor = math.log(LOG_FLOOR)

# s = 0 -> mel exponent -35 -> quantum 2**-35 = 2.91e-11 > 1e-12.
# Here T >= 1 is always above the floor.
logs, floored = log_mel_with_floor([0, 1, 1000], -35, LOG_FLOOR)
eq("s0 T=0 floored", floored[0], True)
eq("s0 T=1 not floored", floored[1], False)
eq("s0 T=1000 not floored", floored[2], False)
close("s0 T=1 value", logs[1], -35 * math.log(2.0), 1e-12)
eq("s0 T=0 value is ln(floor)", logs[0], ln_floor)

# s = 10 -> mel exponent -55 -> quantum 2**-55 = 2.78e-17 < 1e-12.
# A strictly positive T is now BELOW the common floor.  This is the case that
# a "T == 0" shortcut would get wrong.
logs, floored = log_mel_with_floor([0, 1, 10 ** 4, 10 ** 6], -55, LOG_FLOOR)
eq("s10 T=0 floored", floored[0], True)
eq("s10 T=1 floored although positive", floored[1], True)
eq("s10 T=1e4 floored although positive", floored[2], True)
eq("s10 T=1e6 not floored", floored[3], False)
# boundary: smallest T above the floor at exponent -55
thr = Fraction(LOG_FLOOR)
t_min = 1
while Fraction(t_min) * Fraction(1, 2 ** 55) < thr:
    t_min += 1
logs, floored = log_mel_with_floor([t_min - 1, t_min], -55, LOG_FLOOR)
eq("floor boundary below", floored[0], True)
eq("floor boundary at", floored[1], False)
eq("floor boundary value", t_min, 36029)

# the floor decision must be exact, not float-rounded: T just under and just
# over the threshold must differ
eq("exact boundary is a single LSB",
   log_mel_with_floor([t_min - 1], -55, LOG_FLOOR)[1][0] !=
   log_mel_with_floor([t_min], -55, LOG_FLOOR)[1][0], True)

# very large T must not overflow float when logged
big = 2 ** 51
logs, floored = log_mel_with_floor([big], -35, LOG_FLOOR)
close("large T log", logs[0], math.log(big) - 35 * math.log(2.0), 1e-9)

print(f"test_power_mel: {CHECKS} checks, {len(FAILS)} failures")
for f in FAILS:
    print("  FAIL", f)
print("RESULT:", "PASS" if not FAILS else "FAIL")
sys.exit(0 if not FAILS else 1)
