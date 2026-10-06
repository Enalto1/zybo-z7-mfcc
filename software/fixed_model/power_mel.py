"""Integer Power and Mel accumulation, with explicit widths and signedness.

Scale derivation (done here from first principles, then checked numerically by
:func:`check_scale_derivation`; docs/FIXED_POINT_DEVELOPMENT.md section 5 states
the same result and is treated as a claim to verify, not as an assumption).

Let ``u[n]`` be the float64 windowed frame and let the FFT input be

    in[n] = quantize(u[n] * 2**(s-1))   to signed 16 bit, F = 15

so the FFT sees the real value ``u * 2**(s-1)``.  The reused FFT core reduces
by ``S = log2(N)`` bits, so its signed-16/F15 output ``r`` represents

    F_out = r * 2**-15 = DFT(u * 2**(s-1)) * 2**-S
          = DFT(u) * 2**(s-1-S)

The common definition is ``P[k] = |DFT(u)[k]|**2 / N`` (MFCC_SPEC section 3,
power_divisor 512).  With ``psum = re**2 + im**2``:

    |DFT(u)|**2 = (psum * 2**-30) * 2**(2*(S+1-s))
    P           = psum * 2**(2*S + 2 - 2*s - 30 - log2(N))
                = psum * 2**-(19 + 2*s)                  for N = 512, S = 9

Mel accumulation with ``w_int = round(B * 2**Fw)``:

    T_m   = sum_k psum[k] * w_int[m][k]        (exact integer)
    E_m   = T_m * 2**-(19 + 2*s + Fw)

Everything up to ``T_m`` is exact integer arithmetic; no rounding happens
between the FFT output and ``T_m``.  The only quantizations in this stage are
the Mel coefficients themselves.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from .qnum import (OverflowCounter, OverflowPolicy, bits_for_unsigned,
                   resize_unsigned)

__all__ = [
    "PowerMelScales",
    "power_exponent",
    "mel_exponent",
    "power_integer",
    "mel_accumulate",
    "log_mel_with_floor",
    "check_scale_derivation",
    "PSUM_WIDTH_UNSIGNED",
]

# out_re, out_im are signed 16 bit, so psum <= 2*32768**2 = 2**31.
PSUM_MAX = 2 * 32768 * 32768
PSUM_WIDTH_UNSIGNED = bits_for_unsigned(PSUM_MAX)   # 32


@dataclass(frozen=True)
class PowerMelScales:
    """Exponents that turn integers back into common-unit real values."""

    nfft: int
    total_fft_shift_S: int
    bfp_shift_s: int
    mel_frac_bits_Fw: int
    out_frac_bits: int = 15

    @property
    def power_exp_log2(self) -> int:
        """P = psum * 2**power_exp_log2"""
        return power_exponent(self.nfft, self.total_fft_shift_S,
                              self.bfp_shift_s, self.out_frac_bits)

    @property
    def mel_exp_log2(self) -> int:
        """E = T * 2**mel_exp_log2"""
        return self.power_exp_log2 - self.mel_frac_bits_Fw


def power_exponent(nfft: int, total_fft_shift_S: int, bfp_shift_s: int,
                   out_frac_bits: int = 15) -> int:
    """Exponent e with P = psum * 2**e.

        P = psum * 2**(2*S + 2 - 2*s - 2*F - log2 N)

    ``F`` (``out_frac_bits``) is the fraction width of the FFT OUTPUT, not the
    internal datapath width: a wider internal path with the same output format
    does not change this exponent.  The default 15 is the existing signed-16
    Q1.15 output.  The old hard-coded ``-30`` was ``-2*15``; it is wrong for
    any other output format, so the width study must pass this explicitly.
    """
    log2_n = int(round(math.log2(nfft)))
    if (1 << log2_n) != nfft:
        raise ValueError("nfft must be a power of two")
    return (2 * total_fft_shift_S + 2 - 2 * bfp_shift_s
            - 2 * out_frac_bits - log2_n)


def mel_exponent(nfft: int, total_fft_shift_S: int, bfp_shift_s: int,
                 mel_frac_bits_Fw: int, out_frac_bits: int = 15) -> int:
    return power_exponent(nfft, total_fft_shift_S, bfp_shift_s,
                          out_frac_bits) - mel_frac_bits_Fw


def power_integer(out_re, out_im, num_bins: int | None = None,
                  counter: OverflowCounter | None = None,
                  in_width: int = 16,
                  psum_width: int | None = None) -> list[int]:
    """psum[k] = out_re[k]**2 + out_im[k]**2, exact, unsigned 32 bit.

    The squares and the sum are computed in unlimited precision and then
    checked against the declared width with policy ERROR, because the width
    is provably sufficient for signed-16 inputs.  A failure here means the
    caller handed in something wider than the FFT output contract.
    """
    n = len(out_re) if num_bins is None else num_bins
    lo, hi = -(1 << (in_width - 1)), (1 << (in_width - 1)) - 1
    psum = []
    for k in range(n):
        re_v, im_v = int(out_re[k]), int(out_im[k])
        # Review fixed_review_20261004: the old code accepted values outside
        # the signed-in_width FFT output contract (e.g. 32768) because only
        # the RESULT width was checked.  Check the inputs too.
        if not (lo <= re_v <= hi) or not (lo <= im_v <= hi):
            raise OverflowError(
                f"power.psum bin {k}: ({re_v}, {im_v}) outside the signed "
                f"{in_width}-bit FFT output contract [{lo}, {hi}]")
        value = re_v ** 2 + im_v ** 2
        width = psum_width if psum_width is not None else             bits_for_unsigned(2 * (1 << (in_width - 1)) ** 2)
        v, _ = resize_unsigned(value, width,
                               policy=OverflowPolicy.ERROR,
                               site="power.psum", counter=counter)
        psum.append(v)
    return psum


def mel_accumulate(psum, table, accumulator_width: int | None = None,
                   counter: OverflowCounter | None = None) -> list[int]:
    """T_m = sum_k psum[k] * w_int[m][k], exact unsigned integers.

    ``accumulator_width`` defaults to the tight bound computed from this very
    coefficient table, so an unexpected value is reported rather than wrapped.
    """
    if accumulator_width is None:
        accumulator_width = table.accumulator_bound(
            PSUM_MAX)["tight_bound_bits_unsigned"]
    out = []
    for m in range(table.num_filters):
        row = table.weights_int[m]
        acc = 0
        for k in range(table.num_bins):
            w = row[k]
            if w:
                acc += psum[k] * w
        v, _ = resize_unsigned(acc, accumulator_width,
                               policy=OverflowPolicy.ERROR,
                               site="mel.accumulator", counter=counter)
        out.append(v)
    return out


def log_mel_with_floor(mel_int, mel_exp_log2: int, floor: float = 1e-12):
    """ln(max(E, floor)) with E = T * 2**mel_exp_log2.

    The floor comparison is exact: both sides go through Fraction, so the
    decision does not depend on float rounding.

    Important: the comparison is made on the *exponent-corrected* energy.  A
    strictly positive integer ``T`` can still be below the floor whenever the
    BFP exponent makes ``mel_exp_log2`` small enough, so "T == 0" is not a
    valid shortcut for the floor test on a BFP path.
    """
    floor_frac = Fraction(floor)
    if mel_exp_log2 >= 0:
        scale = Fraction(1 << mel_exp_log2, 1)
    else:
        scale = Fraction(1, 1 << -mel_exp_log2)
    logs = []
    floored = []
    for idx, t in enumerate(mel_int):
        t = int(t)
        # Review fixed_review_20261004: a negative value used to be silently
        # floored.  Mel energy is a sum of non-negative products, so a
        # negative here means the caller is wrong; fail instead of hiding it.
        if t < 0:
            raise ValueError(
                f"log_mel_with_floor: band {idx} has negative integer energy "
                f"{t}; Mel accumulation cannot be negative")
        energy = Fraction(t) * scale
        if energy < floor_frac:
            floored.append(True)
            logs.append(math.log(floor))
        else:
            floored.append(False)
            # log of an exact rational, computed without overflowing float:
            # ln(T) + mel_exp_log2 * ln 2
            logs.append(math.log(t) + mel_exp_log2 * math.log(2.0))
    return logs, floored


def check_scale_derivation(nfft: int = 512, total_fft_shift_S: int = 9,
                           bfp_shift_s: int = 0, mel_frac_bits_Fw: int = 16
                           ) -> dict:
    """Self-check of the exponent algebra against the documented values."""
    pe = power_exponent(nfft, total_fft_shift_S, bfp_shift_s)
    me = mel_exponent(nfft, total_fft_shift_S, bfp_shift_s, mel_frac_bits_Fw)
    expected_pe = -(19 + 2 * bfp_shift_s)
    expected_me = -(19 + 2 * bfp_shift_s + mel_frac_bits_Fw)
    return {
        "nfft": nfft,
        "S": total_fft_shift_S,
        "s": bfp_shift_s,
        "Fw": mel_frac_bits_Fw,
        "power_exp_log2": pe,
        "power_exp_expected": expected_pe,
        "power_exp_matches_doc": pe == expected_pe,
        "mel_exp_log2": me,
        "mel_exp_expected": expected_me,
        "mel_exp_matches_doc": me == expected_me,
    }
