#!/usr/bin/env python3
"""Checks for the two contract fixes from review fixed_review_20261004.

(1) Clipping accounting.  "How many frame-samples were clipped" and "how many
    stage clip events happened" are different numbers.  A negative value that
    saturates to -32768 and is then pulled to -32767 by the symmetric clamp is
    ONE clipped sample and TWO events.  The old single counter mixed them.

(2) BFP exponent contract.  The adopted rule is the UNROUNDED comparison
    ``peak * 2**(s-1) * 2**F <= target``; the rounded variant is available as a
    separate rule and is checked to differ exactly where expected.

Run:  python verification/fixed/test_clip_and_shift.py
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.fixed_model.pipeline import (  # noqa: E402
    CANDIDATES, SHIFT_RULE_ROUNDED, SHIFT_RULE_UNROUNDED, CandidateConfig,
    ClipReport, InputQuantConfig, choose_bfp_shift, quantize_frame_input)

FAILS: list[str] = []
CHECKS = 0
Q = InputQuantConfig()
CFG = CANDIDATES["bfp_pre_quant"]


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
    FAILS.append(f"{label}: did not raise {exc.__name__}")


def clip(frame, s=1):
    out, rep = quantize_frame_input(frame, s, Q)
    return out, rep.clipped_frame_samples, rep.total_events


# ================= (1) clipping accounting =================
# s = 1 means scale 2**0, so u * 32768 is the quantizer input.

# negative saturation + symmetric clamp: ONE sample, TWO events.
# This is the exact case the review reported as a double count.
out, n, e = clip([-1.5])
eq("neg sat value", out, [-32767])
eq("neg sat clipped samples", n, 1)
eq("neg sat events", e, 2)

out, n, e = clip([-2.0])
eq("neg sat deep value", out, [-32767])
eq("neg sat deep clipped samples", n, 1)
eq("neg sat deep events", e, 2)

# positive saturation: ONE sample, ONE event (clamp_hi == 32767 == sat value)
out, n, e = clip([1.5])
eq("pos sat value", out, [32767])
eq("pos sat clipped samples", n, 1)
eq("pos sat events", e, 1)

out, n, e = clip([2.0])
eq("pos sat deep clipped samples", n, 1)
eq("pos sat deep events", e, 1)

# exactly -1.0 is representable as -32768, so the WIDTH policy does not fire;
# only the symmetric clamp does.  ONE sample, ONE event.
out, n, e = clip([-1.0])
eq("minus one value", out, [-32767])
eq("minus one clipped samples", n, 1)
eq("minus one events", e, 1)

# boundary inputs that must NOT clip at all
out, n, e = clip([Fraction(32767, 32768)])
eq("max positive value", out, [32767])
eq("max positive no clip", (n, e), (0, 0))
out, n, e = clip([Fraction(-32767, 32768)])
eq("max negative value", out, [-32767])
eq("max negative no clip", (n, e), (0, 0))
out, n, e = clip([0.0])
eq("zero no clip", (n, e), (0, 0))
# just inside: 32766.5/32768 rounds half-even to 32766
out, n, e = clip([Fraction(65533, 65536)])
eq("just inside no clip", (n, e), (0, 0))

# mixed frame: three clipped samples.  1.5 saturates positively (1 event);
# -1.5 and -3.0 each saturate AND then hit the symmetric clamp (2 events each).
# So 3 clipped samples and 5 events.
out, rep = quantize_frame_input([1.5, -1.5, 0.0, Fraction(32767, 32768), -3.0],
                                1, Q)
eq("mixed values", out, [32767, -32767, 0, 32767, -32767])
eq("mixed clipped samples", rep.clipped_frame_samples, 3)
eq("mixed events", rep.total_events, 5)
eq("mixed event sites", rep.events,
   {"quantize_width_policy": 3, "symmetric_clamp": 2})

# the report is self-describing and states the counting unit
d = rep.as_dict()
eq("report keys", sorted(d), ["clip_events_by_stage", "clip_events_total",
                              "clipped_frame_samples", "counting_unit"])
eq("counting unit mentions frame-sample", "frame-sample" in d["counting_unit"],
   True)

# merge keeps both quantities consistent
a, b = ClipReport(), ClipReport()
a.clipped_frame_samples = 2
a.note("x")
b.clipped_frame_samples = 3
b.note("x")
b.note("y")
a.merge(b)
eq("merge samples", a.clipped_frame_samples, 5)
eq("merge events", a.events, {"x": 2, "y": 1})

# clipped samples can never exceed events, and never exceed the frame length
for frame in ([-1.5] * 7, [1.5] * 7, [0.0] * 7, [-1.0, 1.0, 0.5, -0.5]):
    _, rep = quantize_frame_input(frame, 1, Q)
    eq(f"samples <= events len={len(frame)}",
       rep.clipped_frame_samples <= rep.total_events, True)
    eq(f"samples <= frame len={len(frame)}",
       rep.clipped_frame_samples <= len(frame), True)


# ================= (2) BFP exponent contract =================
def peak_frame(num, den=32768):
    return [float(Fraction(num, den))] * 4


def chosen(num, den=32768, rule=SHIFT_RULE_UNROUNDED, cfg=CFG):
    return choose_bfp_shift(peak_frame(num, den), cfg, Q, rule)


T = CFG.target_peak_int
eq("target is 16384", T, 16384)

# just below the target at s = 1
eq("peak 16383 -> s=1", chosen(16383)[0], 1)
# exactly the target at s = 1
eq("peak 16384 (exact boundary) -> s=1", chosen(16384)[0], 1)
# just above: the unrounded rule steps down, the rounded rule does not
eq("peak 16384.25 unrounded -> s=0", chosen(65537, 131072)[0], 0)
eq("peak 16384.25 rounded -> s=1",
   chosen(65537, 131072, SHIFT_RULE_ROUNDED)[0], 1)
# half-LSB tie: round_half_even(16384.5) = 16384 <= target, so the rounded
# rule allows s=1 while the unrounded rule does not.
eq("peak 16384.5 unrounded -> s=0", chosen(32769, 65536)[0], 0)
eq("peak 16384.5 rounded (tie to even) -> s=1",
   chosen(32769, 65536, SHIFT_RULE_ROUNDED)[0], 1)
# a tie that rounds UP is rejected by both rules
# round_half_even(16385.5) = 16386 > 16384
eq("peak 16385.5 unrounded -> s=0", chosen(32771, 65536)[0], 0)
eq("peak 16385.5 rounded -> s=0",
   chosen(32771, 65536, SHIFT_RULE_ROUNDED)[0], 0)

# the two rules agree everywhere outside the half-LSB band above the target
for num in (1, 100, 8191, 8192, 8193, 16383, 16384, 20000, 32767):
    eq(f"rules agree at peak {num}",
       chosen(num)[0], chosen(num, rule=SHIFT_RULE_ROUNDED)[0])

# the adopted rule is never more aggressive than the rounded one
for num, den in ((65537, 131072), (32769, 65536), (16383, 32768),
                 (1, 32768), (32767, 32768)):
    eq(f"unrounded <= rounded at {num}/{den}",
       chosen(num, den)[0] <= chosen(num, den, SHIFT_RULE_ROUNDED)[0], True)

# the contract actually holds: the quantized peak fits the target
for num, den in ((1, 32768), (16383, 32768), (16384, 32768),
                 (65537, 131072), (32767, 32768), (7, 1000)):
    s, _ = chosen(num, den)
    q, _ = quantize_frame_input(peak_frame(num, den), s, Q)
    eq(f"quantized peak <= target at {num}/{den}",
       max(abs(v) for v in q) <= T, True)

# silence
eq("silence returns configured shift", chosen(0)[0], CFG.silence_shift_s)
eq("silence not clamped", chosen(0)[1], False)
eq("empty frame is silence",
   choose_bfp_shift([], CFG, Q)[0], CFG.silence_shift_s)

# bounds: a tiny peak hits shift_max, a huge peak hits shift_min
s, clamped = chosen(1, 1 << 40)
eq("tiny peak hits shift_max", s, CFG.shift_max)
eq("tiny peak reports clamped", clamped, True)
big = CandidateConfig(name="b", mode="bfp_pre_quant", shift_min=0, shift_max=4)
s, clamped = choose_bfp_shift([100.0] * 4, big, Q)
eq("huge peak hits shift_min", s, big.shift_min)
eq("huge peak reports clamped", clamped, True)

# an unknown rule must fail loudly
raises("unknown shift rule", ValueError, choose_bfp_shift,
       peak_frame(1000), CFG, Q, "nonsense")

# t975 candidate uses a different target and must still satisfy its contract
t975 = CANDIDATES["bfp_pre_quant_t975"]
eq("t975 target", t975.target_peak_int, 31949)
for num in (1, 1000, 31948, 31949, 31950):
    s, _ = choose_bfp_shift(peak_frame(num), t975, Q)
    q, _ = quantize_frame_input(peak_frame(num), s, Q)
    eq(f"t975 quantized peak <= target at {num}",
       max(abs(v) for v in q) <= t975.target_peak_int, True)

print(f"test_clip_and_shift: {CHECKS} checks, {len(FAILS)} failures")
for f in FAILS:
    print("  FAIL", f)
print("RESULT:", "PASS" if not FAILS else "FAIL")
sys.exit(0 if not FAILS else 1)
