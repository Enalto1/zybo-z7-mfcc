#!/usr/bin/env python3
"""Independent checks of the fixed-point primitives.

Every expected value below is written out by hand from the definition, not
produced by the code under test.  Covers positive and negative half-LSB ties,
representable boundaries, wrap vs saturate, and overflow detection.

Run:  python verification/fixed/test_qnum.py
Exit: 0 all checks passed, 1 at least one failed.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.fixed_model.qnum import (  # noqa: E402
    OverflowCounter, OverflowPolicy, RoundMode, bits_for_signed,
    bits_for_unsigned, quantize_real, resize_signed, resize_unsigned,
    round_half_away, round_half_even, saturate_to_signed, shift_right_round,
    signed_range, truncate_floor, unsigned_range, wrap_to_signed)

FAILS: list[str] = []
CHECKS = 0


def eq(label, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{label}: got {got!r}, expected {want!r}")


def raises(label, exc, fn, *a, **kw):
    global CHECKS
    CHECKS += 1
    try:
        fn(*a, **kw)
    except exc:
        return
    except Exception as e:                                  # noqa: BLE001
        FAILS.append(f"{label}: raised {type(e).__name__}, expected {exc.__name__}")
        return
    FAILS.append(f"{label}: did not raise {exc.__name__}")


# --------------------------------------------------- nearest, ties to even
# 2.5 -> 2, 3.5 -> 4, -2.5 -> -2, -3.5 -> -4 (tie goes to the even neighbour)
eq("half_even  5/2", round_half_even(5, 2), 2)
eq("half_even  7/2", round_half_even(7, 2), 4)
eq("half_even -5/2", round_half_even(-5, 2), -2)
eq("half_even -7/2", round_half_even(-7, 2), -4)
# non-ties round to the nearest regardless of parity
eq("half_even  1/2", round_half_even(1, 2), 0)
eq("half_even  3/2", round_half_even(3, 2), 2)
eq("half_even  2/3", round_half_even(2, 3), 1)
eq("half_even -2/3", round_half_even(-2, 3), -1)
eq("half_even  1/3", round_half_even(1, 3), 0)
eq("half_even 11/4", round_half_even(11, 4), 3)
eq("half_even  9/4", round_half_even(9, 4), 2)
eq("half_even exact", round_half_even(8, 4), 2)
eq("half_even zero", round_half_even(0, 7), 0)

# --------------------------------------------------- nearest, ties away
eq("half_away  5/2", round_half_away(5, 2), 3)
eq("half_away  7/2", round_half_away(7, 2), 4)
eq("half_away -5/2", round_half_away(-5, 2), -3)
eq("half_away -7/2", round_half_away(-7, 2), -4)
eq("half_away  1/2", round_half_away(1, 2), 1)
eq("half_away -1/2", round_half_away(-1, 2), -1)
eq("half_away  1/3", round_half_away(1, 3), 0)

# the two modes must disagree exactly on ties and agree elsewhere
eq("modes differ on tie", round_half_even(5, 2) != round_half_away(5, 2), True)
eq("modes agree off tie", round_half_even(2, 3) == round_half_away(2, 3), True)

# --------------------------------------------------- truncation toward -inf
eq("floor  5/2", truncate_floor(5, 2), 2)
eq("floor -5/2", truncate_floor(-5, 2), -3)
eq("floor -4/2", truncate_floor(-4, 2), -2)
eq("floor  0/2", truncate_floor(0, 2), 0)
raises("floor den<=0", ValueError, truncate_floor, 1, 0)

# --------------------------------------------------- arithmetic shift right
eq("shr even  5>>1", shift_right_round(5, 1, RoundMode.HALF_EVEN), 2)
eq("shr even  7>>1", shift_right_round(7, 1, RoundMode.HALF_EVEN), 4)
eq("shr even -5>>1", shift_right_round(-5, 1, RoundMode.HALF_EVEN), -2)
eq("shr even -7>>1", shift_right_round(-7, 1, RoundMode.HALF_EVEN), -4)
eq("shr floor -5>>1", shift_right_round(-5, 1, RoundMode.FLOOR), -3)
eq("shr away -5>>1", shift_right_round(-5, 1, RoundMode.HALF_AWAY), -3)
eq("shr zero shift", shift_right_round(-12345, 0), -12345)
# 6 = 0b110; >>2 is 1.5 -> tie -> even -> 2
eq("shr even  6>>2", shift_right_round(6, 2, RoundMode.HALF_EVEN), 2)
# 10 = 0b1010; >>2 is 2.5 -> tie -> even -> 2
eq("shr even 10>>2", shift_right_round(10, 2, RoundMode.HALF_EVEN), 2)
# -6>>2 is -1.5 -> tie -> even -> -2
eq("shr even -6>>2", shift_right_round(-6, 2, RoundMode.HALF_EVEN), -2)
raises("shr negative", ValueError, shift_right_round, 4, -1)

# --------------------------------------------------- ranges
eq("signed 16 range", signed_range(16), (-32768, 32767))
eq("signed 18 range", signed_range(18), (-131072, 131071))
eq("unsigned 32 range", unsigned_range(32), (0, 4294967295))
eq("bits_for_signed 32767", bits_for_signed(32767), 16)
eq("bits_for_signed -32768", bits_for_signed(-32768), 16)
eq("bits_for_signed 32768", bits_for_signed(32768), 17)
eq("bits_for_unsigned 2**31", bits_for_unsigned(2 ** 31), 32)
eq("bits_for_unsigned 2**31-1", bits_for_unsigned(2 ** 31 - 1), 31)

# --------------------------------------------------- wrap (two's complement)
eq("wrap 32767/16", wrap_to_signed(32767, 16), (32767, False))
eq("wrap -32768/16", wrap_to_signed(-32768, 16), (-32768, False))
eq("wrap 32768/16", wrap_to_signed(32768, 16), (-32768, True))
eq("wrap -32769/16", wrap_to_signed(-32769, 16), (32767, True))
eq("wrap 65536/16", wrap_to_signed(65536, 16), (0, True))
# the Type-II boundary the FFT review identified: +2**17 wraps in 18 bits
eq("wrap 131072/18", wrap_to_signed(131072, 18), (-131072, True))
eq("wrap 131071/18", wrap_to_signed(131071, 18), (131071, False))
eq("wrap -131072/18", wrap_to_signed(-131072, 18), (-131072, False))

# --------------------------------------------------- saturate
eq("sat 32768/16", saturate_to_signed(32768, 16), (32767, True))
eq("sat -32769/16", saturate_to_signed(-32769, 16), (-32768, True))
eq("sat 100/16", saturate_to_signed(100, 16), (100, False))

# wrap and saturate must differ on the same out-of-range value
eq("wrap != sat", wrap_to_signed(32768, 16)[0] != saturate_to_signed(32768, 16)[0],
   True)

# --------------------------------------------------- resize + counter
c = OverflowCounter()
eq("resize wrap", resize_signed(32768, 16, OverflowPolicy.WRAP, "a", c),
   (-32768, True))
eq("resize sat", resize_signed(32768, 16, OverflowPolicy.SATURATE, "b", c),
   (32767, True))
eq("resize ok", resize_signed(5, 16, OverflowPolicy.ERROR, "c", c), (5, False))
raises("resize error policy", OverflowError, resize_signed, 32768, 16,
       OverflowPolicy.ERROR, "d", c)
eq("counter sites", c.as_dict(), {"a": 1, "b": 1})
eq("counter total", c.total(), 2)

c2 = OverflowCounter()
c2.merge(c)
c2.note("a", True)
eq("counter merge", c2.as_dict(), {"a": 2, "b": 1})

eq("resize_unsigned ok",
   resize_unsigned(2 ** 31, 32, OverflowPolicy.ERROR, "p"), (2 ** 31, False))
raises("resize_unsigned neg", OverflowError, resize_unsigned, -1, 32,
       OverflowPolicy.ERROR, "p")

# --------------------------------------------------- real -> integer
# The worked example in docs/FIXED_POINT_DEVELOPMENT.md section 2:
# 0.95 at F=15 is 31130 because 0.95*32768 = 31129.6
eq("quantize 0.95 F15", quantize_real(0.95, 15, 16)[0], 31130)
eq("quantize 0.95 reconstruct", 31130 / 32768, 0.95001220703125)

# exact dyadic ties, so the tie decision is real and not a float artefact
# 0.0625 * 2**3 = 0.5  -> half_even -> 0 ; half_away -> 1
eq("quantize tie 0.0625 even",
   quantize_real(0.0625, 3, 8, RoundMode.HALF_EVEN)[0], 0)
eq("quantize tie 0.0625 away",
   quantize_real(0.0625, 3, 8, RoundMode.HALF_AWAY)[0], 1)
# 0.1875 * 2**3 = 1.5 -> half_even -> 2 ; half_away -> 2
eq("quantize tie 0.1875 even",
   quantize_real(0.1875, 3, 8, RoundMode.HALF_EVEN)[0], 2)
# -0.0625 * 2**3 = -0.5 -> half_even -> 0 ; half_away -> -1
eq("quantize tie -0.0625 even",
   quantize_real(-0.0625, 3, 8, RoundMode.HALF_EVEN)[0], 0)
eq("quantize tie -0.0625 away",
   quantize_real(-0.0625, 3, 8, RoundMode.HALF_AWAY)[0], -1)
# -0.1875 * 2**3 = -1.5 -> half_even -> -2
eq("quantize tie -0.1875 even",
   quantize_real(-0.1875, 3, 8, RoundMode.HALF_EVEN)[0], -2)

# boundaries of signed 16 / F15
eq("quantize -1.0 F15", quantize_real(-1.0, 15, 16)[0], -32768)
eq("quantize -1.0 no overflow", quantize_real(-1.0, 15, 16)[1], False)
eq("quantize +1.0 F15 saturates",
   quantize_real(1.0, 15, 16, policy=OverflowPolicy.SATURATE), (32767, True))
eq("quantize +1.0 F15 wraps",
   quantize_real(1.0, 15, 16, policy=OverflowPolicy.WRAP), (-32768, True))
eq("quantize largest ok", quantize_real(Fraction(32767, 32768), 15, 16),
   (32767, False))

# extra_scale_log2 must multiply BEFORE rounding, so it is not the same as
# rounding first and shifting afterwards.
# 0.3 at F=15 with extra_scale 0: 0.3*32768 = 9830.4 -> 9830
eq("quantize 0.3 F15 s0", quantize_real(0.3, 15, 16)[0], 9830)
# with extra_scale_log2 = 3: 0.3*8*32768 = 78643.2 -> saturates at 32767
eq("quantize 0.3 F15 scale3 saturates",
   quantize_real(0.3, 15, 16, policy=OverflowPolicy.SATURATE,
                 extra_scale_log2=3), (32767, True))
# 0.01 F15 s0 -> 327.68 -> 328 ; pre-scaled by 2**5 -> 10485.76 -> 10486
# shifting the s0 result left by 5 would give 328*32 = 10496, which differs.
eq("quantize 0.01 F15 s0", quantize_real(0.01, 15, 16)[0], 328)
eq("quantize 0.01 F15 prescale5",
   quantize_real(0.01, 15, 16, extra_scale_log2=5)[0], 10486)
eq("prescale != postshift", 328 << 5, 10496)
eq("prescale differs from postshift by 10 LSB",
   (328 << 5) - quantize_real(0.01, 15, 16, extra_scale_log2=5)[0], 10)

# negative extra scale
eq("quantize 0.5 F15 scale-1",
   quantize_real(0.5, 15, 16, extra_scale_log2=-1)[0], 8192)

# integer and Fraction inputs
eq("quantize int", quantize_real(1, 0, 8)[0], 1)
eq("quantize Fraction(1,3) F4", quantize_real(Fraction(1, 3), 4, 8)[0], 5)

raises("unknown round mode", ValueError, round_half_even, 1, 0)
raises("bad width", ValueError, signed_range, 0)

print(f"test_qnum: {CHECKS} checks, {len(FAILS)} failures")
for f in FAILS:
    print("  FAIL", f)
print("RESULT:", "PASS" if not FAILS else "FAIL")
sys.exit(0 if not FAILS else 1)
