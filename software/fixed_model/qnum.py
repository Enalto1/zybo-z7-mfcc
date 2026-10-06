"""Explicit fixed-point primitives for the MFCC integer bit model.

Design rules (docs/FIXED_POINT_DEVELOPMENT.md section 4, Q0):

- Every value is a Python ``int`` of unlimited precision.  Full products and
  sums are computed exactly; a width is applied only at an explicit hardware
  boundary by calling :func:`wrap_to_signed` / :func:`saturate_to_signed` or
  one of the ``*_resize`` helpers.
- Rounding and overflow are *separate* decisions.  Each resize takes both a
  rounding mode and an overflow policy and reports whether the policy fired.
- Conversion from a real constant goes through :class:`fractions.Fraction`, so
  the tie decision is made on the exact binary value of the float64 input.
  There is no "build a float array and round once at the end" path here.
- NumPy fixed-width integers are never used for arithmetic, so no implicit
  wrap can happen behind our back.

Sign conventions:

- ``signed W bit`` holds ``-2**(W-1) .. 2**(W-1)-1``.
- ``unsigned W bit`` holds ``0 .. 2**W-1``.
- A stored integer ``q`` with fraction width ``F`` denotes ``q * 2**-F``.  Any
  additional per-frame exponent (BFP) is carried separately, never folded into
  ``F`` silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterable

__all__ = [
    "RoundMode",
    "OverflowPolicy",
    "OverflowCounter",
    "div_round",
    "round_half_even",
    "round_half_away",
    "truncate_floor",
    "shift_right_round",
    "wrap_to_signed",
    "saturate_to_signed",
    "resize_signed",
    "resize_unsigned",
    "quantize_real",
    "signed_range",
    "unsigned_range",
    "bits_for_signed",
    "bits_for_unsigned",
]


class RoundMode:
    """Rounding modes used when discarding fractional bits."""

    HALF_EVEN = "half_even"      # ties to even (convergent); FFT core uses this
    HALF_AWAY = "half_away"      # ties away from zero
    FLOOR = "floor"              # arithmetic shift right / truncate toward -inf
    ALL = (HALF_EVEN, HALF_AWAY, FLOOR)


class OverflowPolicy:
    """What happens when a value does not fit the target width."""

    WRAP = "wrap"                # drop high bits (two's complement); FFT core
    SATURATE = "saturate"        # clamp to the representable end
    ERROR = "error"              # raise; use where overflow must be impossible
    ALL = (WRAP, SATURATE, ERROR)


@dataclass
class OverflowCounter:
    """Collects overflow / saturation events by named site."""

    counts: dict = field(default_factory=dict)

    def note(self, site: str, fired: bool) -> None:
        if fired:
            self.counts[site] = self.counts.get(site, 0) + 1

    def total(self) -> int:
        return sum(self.counts.values())

    def merge(self, other: "OverflowCounter") -> None:
        for site, n in other.counts.items():
            self.counts[site] = self.counts.get(site, 0) + n

    def as_dict(self) -> dict:
        return dict(sorted(self.counts.items()))


# ---------------------------------------------------------------- ranges


def signed_range(width: int) -> tuple[int, int]:
    if width < 1:
        raise ValueError(f"signed width must be >= 1, got {width}")
    return -(1 << (width - 1)), (1 << (width - 1)) - 1


def unsigned_range(width: int) -> tuple[int, int]:
    if width < 1:
        raise ValueError(f"unsigned width must be >= 1, got {width}")
    return 0, (1 << width) - 1


def bits_for_signed(value: int) -> int:
    """Minimum signed width that holds ``value``."""
    if value < 0:
        return (-value - 1).bit_length() + 1
    return value.bit_length() + 1


def bits_for_unsigned(value: int) -> int:
    if value < 0:
        raise ValueError("bits_for_unsigned needs a non-negative value")
    return max(1, value.bit_length())


# ---------------------------------------------------------------- rounding


def truncate_floor(num: int, den: int) -> int:
    """floor(num/den) for den > 0.  Python // already floors toward -inf."""
    if den <= 0:
        raise ValueError("denominator must be positive")
    return num // den


def round_half_even(num: int, den: int) -> int:
    """Exact nearest rounding of num/den, ties to even.  den > 0."""
    if den <= 0:
        raise ValueError("denominator must be positive")
    q, r = divmod(num, den)          # r in [0, den)
    twice = 2 * r
    if twice > den:
        return q + 1
    if twice < den:
        return q
    return q + 1 if (q & 1) else q   # exact tie -> make the result even


def round_half_away(num: int, den: int) -> int:
    """Exact nearest rounding of num/den, ties away from zero.  den > 0."""
    if den <= 0:
        raise ValueError("denominator must be positive")
    if num >= 0:
        q, r = divmod(num, den)
        return q + 1 if 2 * r >= den else q
    q, r = divmod(-num, den)
    return -(q + 1) if 2 * r >= den else -q


def div_round(num: int, den: int, mode: str = RoundMode.HALF_EVEN) -> int:
    if mode == RoundMode.HALF_EVEN:
        return round_half_even(num, den)
    if mode == RoundMode.HALF_AWAY:
        return round_half_away(num, den)
    if mode == RoundMode.FLOOR:
        return truncate_floor(num, den)
    raise ValueError(f"unknown round mode {mode!r}")


def shift_right_round(value: int, shift: int,
                      mode: str = RoundMode.HALF_EVEN) -> int:
    """Discard ``shift`` low bits of ``value`` with the given rounding mode.

    ``shift == 0`` returns the value unchanged.  Negative shift is rejected:
    a left shift never discards bits and must be written explicitly.
    """
    if shift < 0:
        raise ValueError("shift_right_round does not take a negative shift")
    if shift == 0:
        return value
    return div_round(value, 1 << shift, mode)


# ---------------------------------------------------------------- resizing


def wrap_to_signed(value: int, width: int) -> tuple[int, bool]:
    """Two's-complement wrap into signed ``width``.  Returns (value, fired)."""
    lo, hi = signed_range(width)
    if lo <= value <= hi:
        return value, False
    span = 1 << width
    wrapped = ((value - lo) % span) + lo
    return wrapped, True


def saturate_to_signed(value: int, width: int) -> tuple[int, bool]:
    lo, hi = signed_range(width)
    if value < lo:
        return lo, True
    if value > hi:
        return hi, True
    return value, False


def resize_signed(value: int, width: int,
                  policy: str = OverflowPolicy.WRAP,
                  site: str = "", counter: OverflowCounter | None = None
                  ) -> tuple[int, bool]:
    """Apply a signed hardware width to an exact integer."""
    if policy not in OverflowPolicy.ALL:
        raise ValueError(f"unknown overflow policy {policy!r}")
    if policy == OverflowPolicy.WRAP:
        out, fired = wrap_to_signed(value, width)
    elif policy == OverflowPolicy.SATURATE:
        out, fired = saturate_to_signed(value, width)
    elif policy == OverflowPolicy.ERROR:
        lo, hi = signed_range(width)
        if not (lo <= value <= hi):
            raise OverflowError(
                f"{site or 'value'} {value} does not fit signed {width} bits "
                f"[{lo}, {hi}]")
        out, fired = value, False
    else:
        raise ValueError(f"unknown overflow policy {policy!r}")
    if counter is not None:
        counter.note(site or f"signed{width}", fired)
    return out, fired


def resize_unsigned(value: int, width: int,
                    policy: str = OverflowPolicy.ERROR,
                    site: str = "", counter: OverflowCounter | None = None
                    ) -> tuple[int, bool]:
    if policy not in OverflowPolicy.ALL:
        raise ValueError(f"unknown overflow policy {policy!r}")
    lo, hi = unsigned_range(width)
    fired = not (lo <= value <= hi)
    if not fired:
        out = value
    elif policy == OverflowPolicy.WRAP:
        out = value & hi
    elif policy == OverflowPolicy.SATURATE:
        out = lo if value < lo else hi
    elif policy == OverflowPolicy.ERROR:
        raise OverflowError(
            f"{site or 'value'} {value} does not fit unsigned {width} bits "
            f"[{lo}, {hi}]")
    else:
        raise ValueError(f"unknown overflow policy {policy!r}")
    if counter is not None:
        counter.note(site or f"unsigned{width}", fired)
    return out, fired


# ---------------------------------------------------------------- real -> int


def quantize_real(x, frac_bits: int, width: int,
                  mode: str = RoundMode.HALF_EVEN,
                  policy: str = OverflowPolicy.SATURATE,
                  site: str = "", counter: OverflowCounter | None = None,
                  extra_scale_log2: int = 0) -> tuple[int, bool]:
    """Quantize a real value to signed ``width`` with ``frac_bits`` fraction.

    ``x`` may be ``int``, ``float`` or ``Fraction``.  A float is converted
    through :class:`Fraction`, i.e. its exact binary value, so a tie is a real
    tie and not a float rounding artefact.

    ``extra_scale_log2`` multiplies ``x`` by ``2**extra_scale_log2`` *before*
    quantizing.  This is the block-floating-point entry point: the exponent is
    chosen on the wide value and a single quantization follows.  It is not the
    same operation as quantizing first and shifting afterwards.
    """
    value = Fraction(x) * _pow2(frac_bits + extra_scale_log2)
    q = div_round(value.numerator, value.denominator, mode)
    return resize_signed(q, width, policy=policy, site=site or "quantize",
                         counter=counter)


def _pow2(exp: int) -> Fraction:
    return Fraction(1 << exp, 1) if exp >= 0 else Fraction(1, 1 << -exp)


def quantize_sequence(values: Iterable, frac_bits: int, width: int,
                      mode: str = RoundMode.HALF_EVEN,
                      policy: str = OverflowPolicy.SATURATE,
                      site: str = "", counter: OverflowCounter | None = None,
                      extra_scale_log2: int = 0) -> list[int]:
    out = []
    for v in values:
        q, _ = quantize_real(v, frac_bits, width, mode=mode, policy=policy,
                             site=site, counter=counter,
                             extra_scale_log2=extra_scale_log2)
        out.append(q)
    return out
