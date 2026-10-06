"""EXPERIMENTAL width-parameterised R2^2SDF model.  NOT a verified RTL model.

``fft_bitmodel.py`` is the model that was shown bit-exact against the existing
RTL (29 XSIM scenarios, two Vivado versions; see PROVENANCE.json).  That file
is untouched.  This module is a *study* model: it reproduces the same
algorithm with the widths exposed as parameters so the effect of data
precision can be measured before anyone decides to change RTL.

**Only the 16-bit configuration corresponds to existing hardware.**  Any wider
configuration here has no RTL, no simulation and no synthesis behind it, and
must never be described as "the verified model".

Widths are kept separate on purpose (the study needs them separable):

  ``data_width`` / ``data_frac``      value carried across group boundaries
  ``stage1_growth``/``stage2_growth`` bits gained inside a group
  ``twiddle_width``/``twiddle_frac``  coefficient precision
  ``group_shift`` / ``trailing_shift``bits removed per complete / trailing group
  ``output_width``/``output_frac``    final requantisation after the last group

Scale: width growth supplies guard bits and does not change the binary point.
The physical FFT normalisation is determined only by ``group_shift`` and
``trailing_shift``.  With the default shifts (2, 1), ``S = log2(N)``.
Output ``data_frac -> out_f`` requantisation changes the integer code, not the
physical FFT normalisation.  Use :func:`physical_fft_shift` with ``out_f`` to
recover units; never combine :func:`integer_code_shift` with ``out_f``.
"""

from __future__ import annotations

from dataclasses import dataclass
from operator import index

from .qnum import round_half_even, wrap_to_signed

__all__ = [
    "FftWidthConfig",
    "BASELINE_16BIT",
    "physical_fft_shift",
    "output_requant_shift",
    "integer_code_shift",
    "total_shift",
    "psum_max_for",
    "fft_fixed_wide",
    "fft_fixed_natural_wide",
    "bit_reverse",
]


@dataclass(frozen=True)
class FftWidthConfig:
    data_width: int = 16
    data_frac: int = 15
    stage1_growth: int = 1
    stage2_growth: int = 1
    twiddle_width: int = 16
    twiddle_frac: int = 15
    group_shift: int = 2
    trailing_shift: int = 1
    output_width: int | None = None     # None -> data_width
    output_frac: int | None = None      # None -> data_frac
    label: str = "baseline16"

    def __post_init__(self) -> None:
        # This study supports signed formats with at least a sign bit, a
        # non-negative binary point, and right-shift-only output resizing.
        for name in ("data_width", "data_frac", "stage1_growth", "stage2_growth",
                     "twiddle_width", "twiddle_frac", "group_shift",
                     "trailing_shift", "output_width", "output_frac"):
            value = getattr(self, name)
            if value is None and name in ("output_width", "output_frac"):
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        for name, width, frac in (("data", self.data_width, self.data_frac),
                                  ("twiddle", self.twiddle_width, self.twiddle_frac),
                                  ("output", self.out_w, self.out_f)):
            if width < 2 or not 0 <= frac < width:
                raise ValueError(f"{name} requires W >= 2 and 0 <= F < W")
        if min(self.stage1_growth, self.stage2_growth,
               self.group_shift, self.trailing_shift) < 0:
            raise ValueError("growth and shift counts must be non-negative")
        if self.out_f > self.data_frac:
            raise ValueError("output_frac larger than data_frac is not modelled")

    @property
    def out_w(self) -> int:
        return self.data_width if self.output_width is None else self.output_width

    @property
    def out_f(self) -> int:
        return self.data_frac if self.output_frac is None else self.output_frac

    @property
    def stage1_width(self) -> int:
        return self.data_width + self.stage1_growth

    @property
    def stage2_width(self) -> int:
        return self.stage1_width + self.stage2_growth

    def describe(self) -> dict:
        return {
            "label": self.label,
            "data_width": self.data_width,
            "data_frac": self.data_frac,
            "stage1_width": self.stage1_width,
            "stage2_width": self.stage2_width,
            "twiddle_width": self.twiddle_width,
            "twiddle_frac": self.twiddle_frac,
            "twiddle_product_width": self.stage2_width + self.twiddle_width,
            "twiddle_accumulator_width": self.stage2_width + self.twiddle_width + 1,
            "group_shift": self.group_shift,
            "trailing_shift": self.trailing_shift,
            "output_width": self.out_w,
            "output_frac": self.out_f,
            "output_requant_shift_bits": output_requant_shift(self),
            "data_signed": True,
            "twiddle_signed": True,
            "output_signed": True,
            "scale_contract": "physical_fft_shift S with output F; integer_code_shift with data F",
            "arithmetic": "Python unlimited integers; full twiddle products and sum before rounding",
            "input_contract": "signed integers already within data_width; invalid inputs rejected",
            "rounding": "ties_to_even at every bit-reducing boundary",
            "overflow": "wrap (two's complement), detected and reported",
            "rtl_status": ("corresponds to existing RTL"
                           if self.is_baseline() else
                           "STUDY ONLY - no RTL, no simulation, no synthesis"),
        }

    def is_baseline(self) -> bool:
        return (self.data_width == 16 and self.data_frac == 15
                and self.stage1_growth == 1 and self.stage2_growth == 1
                and self.twiddle_width == 16 and self.twiddle_frac == 15
                and self.group_shift == 2 and self.trailing_shift == 1
                and self.out_w == 16 and self.out_f == 15)


BASELINE_16BIT = FftWidthConfig(label="baseline16")


def _log2_supported(n: int) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < 8 or n > 1024:
        raise ValueError(f"unsupported length {n}; expected a power of two in 8..1024")
    log2n = n.bit_length() - 1
    if (1 << log2n) != n:
        raise ValueError("n must be a power of two")
    return log2n


def physical_fft_shift(n: int, cfg: FftWidthConfig = BASELINE_16BIT) -> int:
    """Physical FFT normalisation S: decoded output approximates FFT(input)/2**S.

    Width growth does not rescale values.  The output F change also preserves
    decoded units (up to rounding/wrap) and is excluded.  At N=512, S=9 for
    the study's default group/trailing shifts, including F19 -> F15 output.
    """
    log2n = _log2_supported(n)
    groups, trailing = divmod(log2n, 2)
    return groups * cfg.group_shift + trailing * cfg.trailing_shift


def output_requant_shift(cfg: FftWidthConfig) -> int:
    """Integer right shift for the final data_frac -> output_frac conversion."""
    return cfg.data_frac - cfg.out_f


def integer_code_shift(n: int, cfg: FftWidthConfig = BASELINE_16BIT) -> int:
    """Total net integer-code shift, for use with the original ``data_frac``.

    q_out * 2**(integer_code_shift-data_frac) and
    q_out * 2**(physical_fft_shift-out_f) recover the same FFT units.
    Twiddle F removal decodes the coefficient product; it is not a gain term.
    """
    return physical_fft_shift(n, cfg) + output_requant_shift(cfg)


def total_shift(n: int, cfg: FftWidthConfig) -> int:
    """Legacy alias for integer_code_shift; this is NOT physical FFT scale S.

    Preserved for callers needing the prior integer-code convention.  New
    scale metadata should name both physical_fft_shift and output_requant_shift.
    """
    return integer_code_shift(n, cfg)


def psum_max_for(cfg: FftWidthConfig) -> int:
    """Largest re^2 + im^2 for this output width."""
    half = 1 << (cfg.out_w - 1)
    return 2 * half * half


def _rs(value: int, shift: int, width: int) -> tuple[int, bool]:
    """Round (ties to even) by `shift` bits then wrap into signed `width`."""
    if shift:
        value = round_half_even(value, 1 << shift)
    return wrap_to_signed(value, width)


def _signed_codes(values, width: int, name: str) -> list[int]:
    """Validate port/ROM codes without silently truncating float inputs."""
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    result = []
    for ordinal, value in enumerate(values):
        try:
            code = index(value)
        except TypeError as exc:
            raise TypeError(f"{name}[{ordinal}] must be an integer code") from exc
        if not lo <= code <= hi:
            raise ValueError(f"{name}[{ordinal}]={code} outside signed {width} bits")
        result.append(code)
    return result


class _Flags:
    def __init__(self) -> None:
        self.overflow = False


def _group(block_re, block_im, L: int, tw_re, tw_im, cfg: FftWidthConfig,
           flags: _Flags):
    """One complete R2^2 group: Type-I, Type-II, twiddle, group shift."""
    h, q = L // 2, L // 4
    w1, w2 = cfg.stage1_width, cfg.stage2_width

    a_re = [0] * L
    a_im = [0] * L
    for n in range(h):
        for idx, val in ((n, block_re[n] + block_re[n + h]),
                         (h + n, block_re[n] - block_re[n + h])):
            a_re[idx], ov = wrap_to_signed(val, w1)
            flags.overflow |= ov
        for idx, val in ((n, block_im[n] + block_im[n + h]),
                         (h + n, block_im[n] - block_im[n + h])):
            a_im[idx], ov = wrap_to_signed(val, w1)
            flags.overflow |= ov

    c_re = [0] * L
    c_im = [0] * L
    for n in range(q):
        d_re, d_im = a_re[n], a_im[n]
        x_re, x_im = a_re[n + q], a_im[n + q]
        for idx, rr, ii in ((n, d_re + x_re, d_im + x_im),
                            (q + n, d_re - x_re, d_im - x_im)):
            c_re[idx], ov = wrap_to_signed(rr, w2)
            flags.overflow |= ov
            c_im[idx], ov = wrap_to_signed(ii, w2)
            flags.overflow |= ov
        d_re, d_im = a_re[2 * q + n], a_im[2 * q + n]
        x_re, x_im = a_re[3 * q + n], a_im[3 * q + n]
        r_re, r_im = x_im, -x_re          # rotate by -j
        for idx, rr, ii in ((2 * q + n, d_re + r_re, d_im + r_im),
                            (3 * q + n, d_re - r_re, d_im - r_im)):
            c_re[idx], ov = wrap_to_signed(rr, w2)
            flags.overflow |= ov
            c_im[idx], ov = wrap_to_signed(ii, w2)
            flags.overflow |= ov

    out_re = [0] * L
    out_im = [0] * L
    branch = (0, 2, 1, 3)
    for quarter in range(4):
        d = branch[quarter]
        for n in range(q):
            idx = quarter * q + n
            zr, zi = c_re[idx], c_im[idx]
            if q == 1:
                mr, mi = zr, zi            # L == 4 terminal group: no multiply
            else:
                addr = ((d * n) << (8 - (q.bit_length() - 1))) & 0x3FF
                wr, wi = int(tw_re[addr]), int(tw_im[addr])
                mr, ov = _rs(zr * wr - zi * wi, cfg.twiddle_frac, w2)
                flags.overflow |= ov
                mi, ov = _rs(zr * wi + zi * wr, cfg.twiddle_frac, w2)
                flags.overflow |= ov
            out_re[idx], ov = _rs(mr, cfg.group_shift, cfg.data_width)
            flags.overflow |= ov
            out_im[idx], ov = _rs(mi, cfg.group_shift, cfg.data_width)
            flags.overflow |= ov
    return out_re, out_im


def fft_fixed_wide(x_re, x_im, tw_re, tw_im,
                   cfg: FftWidthConfig = BASELINE_16BIT):
    """Integer study output in BIT-REVERSED order and a sticky overflow flag.

    Inputs/ROM entries must already fit their declared signed widths.  Invalid
    ports are rejected; in-model stage/output overflow wraps and sets the flag.
    Only BASELINE_16BIT has comparison evidence against the existing RTL model.
    """
    n = len(x_re)
    _log2_supported(n)
    if len(x_im) != n:
        raise ValueError("real and imaginary input lengths must match")
    if len(tw_re) != 1024 or len(tw_im) != 1024:
        raise ValueError("both twiddle arrays must contain exactly 1024 codes")
    cur_re = _signed_codes(x_re, cfg.data_width, "x_re")
    cur_im = _signed_codes(x_im, cfg.data_width, "x_im")
    tw_re = _signed_codes(tw_re, cfg.twiddle_width, "tw_re")
    tw_im = _signed_codes(tw_im, cfg.twiddle_width, "tw_im")
    flags = _Flags()

    L = n
    while L >= 4:
        nxt_re = [0] * n
        nxt_im = [0] * n
        for base in range(0, n, L):
            br, bi = _group(cur_re[base:base + L], cur_im[base:base + L],
                            L, tw_re, tw_im, cfg, flags)
            nxt_re[base:base + L] = br
            nxt_im[base:base + L] = bi
        cur_re, cur_im = nxt_re, nxt_im
        L //= 4

    if L == 2:
        nxt_re = [0] * n
        nxt_im = [0] * n
        for base in range(0, n, 2):
            for comp, cur, dst in ((0, cur_re, nxt_re), (1, cur_im, nxt_im)):
                s_val, ov = wrap_to_signed(cur[base] + cur[base + 1],
                                           cfg.stage1_width)
                flags.overflow |= ov
                d_val, ov = wrap_to_signed(cur[base] - cur[base + 1],
                                           cfg.stage1_width)
                flags.overflow |= ov
                s_val, ov = _rs(s_val, cfg.trailing_shift, cfg.data_width)
                flags.overflow |= ov
                d_val, ov = _rs(d_val, cfg.trailing_shift, cfg.data_width)
                flags.overflow |= ov
                dst[base], dst[base + 1] = s_val, d_val
        cur_re, cur_im = nxt_re, nxt_im

    # final requantisation to the declared output format
    extra = output_requant_shift(cfg)
    if extra or cfg.out_w != cfg.data_width:
        for i in range(n):
            cur_re[i], ov = _rs(cur_re[i], extra, cfg.out_w)
            flags.overflow |= ov
            cur_im[i], ov = _rs(cur_im[i], extra, cfg.out_w)
            flags.overflow |= ov

    return cur_re, cur_im, flags.overflow


def bit_reverse(index: int, width: int) -> int:
    out = 0
    for b in range(width):
        if index & (1 << b):
            out |= 1 << (width - 1 - b)
    return out


def fft_fixed_natural_wide(x_re, x_im, tw_re, tw_im,
                           cfg: FftWidthConfig = BASELINE_16BIT):
    """Natural bin order (what the ping-pong reorder produces)."""
    n = len(x_re)
    log2n = n.bit_length() - 1
    br, bi, ov = fft_fixed_wide(x_re, x_im, tw_re, tw_im, cfg)
    nr = [0] * n
    ni = [0] * n
    for ordinal in range(n):
        b = bit_reverse(ordinal, log2n)
        nr[b] = br[ordinal]
        ni[b] = bi[ordinal]
    return nr, ni, ov
