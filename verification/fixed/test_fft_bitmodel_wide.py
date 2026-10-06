#!/usr/bin/env python3
"""Reproducible regression for the experimental width-parameterised FFT.

Run from the repository: python verification/fixed/test_fft_bitmodel_wide.py

This proves agreement with the preserved Python model for the baseline only;
it is not RTL simulation or validation of a wider hardware implementation.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from software.fixed_model.fft_bitmodel import (  # noqa: E402
    fft_fixed, fft_fixed_natural, load_twiddle_rom)
from software.fixed_model.fft_bitmodel_wide import (  # noqa: E402
    BASELINE_16BIT, FftWidthConfig, _rs, fft_fixed_wide,
    fft_fixed_natural_wide, integer_code_shift, output_requant_shift,
    physical_fft_shift, psum_max_for, total_shift)

MODEL = ROOT / "software/fixed_model/fft_bitmodel.py"
ROM = ROOT / "software/fixed_model/coefficients/twiddle_1024_w16.mem"
ORIGINAL_SHA256 = "87bb1ea7d956ba8ea91b3e635d77c53739b387a313522809a38621cd25a0438a"
CHECKS = 0
FAILS: list[str] = []


def eq(label, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{label}: got {got!r}, expected {want!r}")


def raises(label, exception, operation):
    global CHECKS
    CHECKS += 1
    try:
        operation()
    except exception:
        return
    except Exception as exc:
        FAILS.append(f"{label}: wrong exception {type(exc).__name__}: {exc}")
    else:
        FAILS.append(f"{label}: did not raise {exception.__name__}")


def main():
    eq("preserved baseline source hash", hashlib.sha256(MODEL.read_bytes()).hexdigest(),
       ORIGINAL_SHA256)
    tw_re, tw_im = load_twiddle_rom(ROM)
    rng = np.random.default_rng(20261004)

    # Seven different vectors at each of all eight supported lengths.  Full
    # signed-range stress and complex inputs include overflow-flag comparison.
    vectors = 0
    overflow_vectors = 0
    for log2n in range(3, 11):
        n = 1 << log2n
        zero = [0] * n
        impulse = [32767] + [0] * (n - 1)
        tone = np.rint(25000 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.int64)
        real_random = rng.integers(-8192, 8193, size=n)
        bipolar = rng.choice([-32768, 32767], size=n)
        complex_re = rng.integers(-32768, 32768, size=n)
        complex_im = rng.integers(-32768, 32768, size=n)
        for name, re_in, im_in in (
                ("zero", zero, zero), ("impulse", impulse, zero),
                ("DC", [16384] * n, zero), ("tone", tone, zero),
                ("random", real_random, zero), ("bipolar", bipolar, zero),
                ("complex", complex_re, complex_im)):
            old = fft_fixed(re_in, im_in, tw_re, tw_im)
            new = fft_fixed_wide(re_in, im_in, tw_re, tw_im)
            eq(f"N={n} {name} bit-reversed values + sticky overflow", new, old)
            eq(f"N={n} {name} natural values + sticky overflow",
               fft_fixed_natural_wide(re_in, im_in, tw_re, tw_im),
               fft_fixed_natural(re_in, im_in, tw_re, tw_im))
            vectors += 1
            overflow_vectors += int(old[2])
        eq(f"baseline N={n} physical S", physical_fft_shift(n), log2n)
    eq("baseline deterministic regression vector count", vectors, 56)
    eq("baseline regression includes observed overflow", overflow_vectors > 0, True)

    # F19 -> F15 is a code conversion, not an additional physical /16 gain.
    wide_to_16 = FftWidthConfig(data_width=20, data_frac=19,
                               output_width=16, output_frac=15, label="wide_to_16")
    eq("F19 to F15 physical S", physical_fft_shift(512, wide_to_16), 9)
    eq("F19 to F15 requant shift", output_requant_shift(wide_to_16), 4)
    eq("F19 to F15 integer code shift", integer_code_shift(512, wide_to_16), 13)
    eq("legacy total_shift is explicitly integer shift", total_shift(512, wide_to_16), 13)
    eq("metadata requant shift", wide_to_16.describe()["output_requant_shift_bits"], 4)
    eq("wider candidate is not RTL verified", wide_to_16.is_baseline(), False)
    changed_shifts = replace(wide_to_16, group_shift=1, trailing_shift=0)
    eq("different internal group shifts change physical gain", physical_fft_shift(512, changed_shifts), 4)
    eq("guard growth does not change physical gain",
       physical_fft_shift(512, replace(wide_to_16, stage1_growth=3, stage2_growth=4)), 9)

    # An exact impulse amplitude 0.5 has DFT 0.5 in every bin.  Both formats
    # recover it, and independent rational power calculation catches counting
    # the four requantisation bits twice (power would be 256 times too large).
    for cfg in (BASELINE_16BIT, wide_to_16,
                FftWidthConfig(data_width=20, data_frac=19, label="wide20")):
        re_out, im_out, overflow = fft_fixed_natural_wide(
            [1 << (cfg.data_frac - 1)] + [0] * 511, [0] * 512, tw_re, tw_im, cfg)
        eq(f"{cfg.label} impulse no overflow", overflow, False)
        eq(f"{cfg.label} impulse imaginary zero", set(im_out), {0})
        eq(f"{cfg.label} impulse code", set(re_out), {1 << (cfg.out_f - 10)})
        q = re_out[0]
        common_fft = Fraction(q) * Fraction(2) ** (physical_fft_shift(512, cfg) - cfg.out_f)
        eq(f"{cfg.label} decoded impulse DFT", common_fft, Fraction(1, 2))
        via_code_shift = Fraction(q) * Fraction(2) ** (integer_code_shift(512, cfg) - cfg.data_frac)
        eq(f"{cfg.label} two code conventions agree", via_code_shift, common_fft)
        for bfp_s in (-2, 0, 3, 24):
            # q denotes FFT(u * 2**(s-1))/2**S at output F.
            fft_u = common_fft * Fraction(2) ** (1 - bfp_s)
            direct_power = fft_u * fft_u / 512
            p_exp = 2 * physical_fft_shift(512, cfg) + 2 - 2 * bfp_s - 2 * cfg.out_f - 9
            eq(f"{cfg.label} s={bfp_s} physical power exponent",
               Fraction(q * q) * Fraction(2) ** p_exp, direct_power)
        if cfg is wide_to_16:
            wrong_fft = Fraction(q) * Fraction(2) ** (integer_code_shift(512, cfg) - cfg.out_f)
            eq("negative control rejects double-counted requantisation", wrong_fft != common_fft, True)
            eq("negative control power factor is 256", (wrong_fft / common_fft) ** 2, 256)

    # Hand-derived positive and negative ties, including after 64-bit precision.
    for value, expected in ((5, 2), (7, 4), (-5, -2), (-7, -4)):
        eq(f"signed ties-to-even {value}/2", _rs(value, 1, 16), (expected, False))
    eq("round then wrap instead of saturate", _rs(15, 1, 4), (-8, True))
    huge = (1 << 90) + 3
    eq("integer precision above int64", _rs(huge, 1, 100), ((1 << 89) + 2, False))
    for q_in, expected in ((320, 2), (448, 4), (-320, -2), (-448, -4)):
        re_out, im_out, overflow = fft_fixed_natural_wide(
            [q_in] + [0] * 7, [0] * 8, tw_re, tw_im, wide_to_16)
        eq(f"final F19 to F15 ties for impulse {q_in}", set(re_out), {expected})
        eq(f"final F19 to F15 ties preserve sign/flag {q_in}", (set(im_out), overflow), ({0}, False))

    # Output overflow is sticky for one call only and uses wrap.  Keeping F=0
    # isolates narrowing from any final fractional rounding.
    narrow = FftWidthConfig(data_frac=0, output_width=2, output_frac=0, label="overflow_probe")
    re_out, im_out, overflow = fft_fixed_natural_wide([8] * 8, [0] * 8, tw_re, tw_im, narrow)
    eq("overflow at output narrowing wraps bin0 8 to 0", re_out[0], 0)
    eq("output narrowing reports sticky overflow", overflow, True)
    eq("overflow resets on next call",
       fft_fixed_natural_wide([0] * 8, [0] * 8, tw_re, tw_im, narrow)[2], False)
    no_guard = FftWidthConfig(stage1_growth=0, stage2_growth=0)
    eq("insufficient stage guard reports overflow",
       fft_fixed_natural_wide([32767] * 8, [0] * 8, tw_re, tw_im, no_guard)[2], True)
    for width in (16, 18, 20, 70):
        cfg = FftWidthConfig(data_width=width, data_frac=width - 1)
        eq(f"signed W{width} square sum unsigned bound", psum_max_for(cfg), 1 << (2 * width - 1))
        eq(f"signed W{width} product width", cfg.describe()["twiddle_product_width"], width + 18)
        eq(f"signed W{width} full sum width", cfg.describe()["twiddle_accumulator_width"], width + 19)
    huge_cfg = FftWidthConfig(data_width=70, data_frac=69)
    huge_re, huge_im, huge_ov = fft_fixed_natural_wide(
        [1 << 67] + [0] * 7, [0] * 8, tw_re, tw_im, huge_cfg)
    eq("FFT with codes above int64 uses Python ints", all(type(x) is int for x in huge_re + huge_im), True)
    eq("FFT large-code output remains above int64", max(huge_re) > (1 << 63) - 1, True)
    eq("FFT large-code no overflow", huge_ov, False)

    # Invalid API inputs fail explicitly instead of silently losing data.
    for kwargs in ({"data_width": 1}, {"data_frac": -1}, {"stage1_growth": -1},
                   {"group_shift": -1}, {"twiddle_frac": 16},
                   {"output_width": 17, "output_frac": 16}):
        raises(f"reject config {kwargs}", ValueError, lambda kwargs=kwargs: FftWidthConfig(**kwargs))
    raises("reject noninteger config", TypeError, lambda: FftWidthConfig(data_width=16.0))
    for n in (0, 4, 9, 2048):
        raises(f"reject unsupported N={n}", ValueError,
               lambda n=n: fft_fixed_wide([0] * n, [0] * n, tw_re, tw_im))
    raises("reject mismatched imaginary length", ValueError,
           lambda: fft_fixed_wide([0] * 8, [0] * 7, tw_re, tw_im))
    raises("reject float integer codes", TypeError,
           lambda: fft_fixed_wide([0.5] * 8, [0] * 8, tw_re, tw_im))
    raises("reject out-of-port-range codes", ValueError,
           lambda: fft_fixed_wide([32768] * 8, [0] * 8, tw_re, tw_im))
    raises("reject incomplete ROM", ValueError,
           lambda: fft_fixed_wide([0] * 8, [0] * 8, tw_re[:-1], tw_im))
    raises("reject out-of-ROM-range codes", ValueError,
           lambda: fft_fixed_wide([0] * 8, [0] * 8, [32768] * 1024, tw_im))
    eq("baseline hash remains unchanged after regression",
       hashlib.sha256(MODEL.read_bytes()).hexdigest(), ORIGINAL_SHA256)
    print(f"test_fft_bitmodel_wide: {CHECKS} checks, {len(FAILS)} failures")
    print(f"Baseline comparison: {vectors} vectors x 2 output orders; {overflow_vectors} overflow vectors")
    for failure in FAILS:
        print("  FAIL", failure)
    print("RESULT:", "PASS" if not FAILS else "FAIL")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
