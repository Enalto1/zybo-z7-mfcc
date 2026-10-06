"""Mixed-precision MFCC pilot pipeline (Q1).

This is NOT a finished fixed-point MFCC.  Per docs/FIXED_POINT_DEVELOPMENT.md
section 4 (Q1) the stages are:

    float64 pre-emphasis / framing / window      <- shared with the reference
    -> FFT input quantization (signed 16, F15)   <- integer
    -> reused FFT bit model                      <- integer, wrap, ties-to-even
    -> integer Power and Mel accumulation        <- integer, exact
    -> float64 log floor and DCT                 <- NOT yet quantized (Q2)

Every run is labelled ``mixed_precision`` for that reason.

Candidates
----------
``fixed_alpha_half``  s = 0 for every frame, so the FFT input is ``u/2``.
``bfp_pre_quant``     the frame exponent ``s`` is chosen from the float64
                      windowed frame and a single quantization follows.  The
                      exponent is picked *before* quantizing.
``q15_then_shift``    auxiliary only: quantize at alpha = 1 first, then shift
                      the integers left.  This cannot recover information that
                      the first quantization already discarded; it exists so
                      the earlier experiment's actual operation is represented
                      honestly and is distinguishable from ``bfp_pre_quant``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

from .coeffs import MelTable
from .fft_bitmodel import fft_fixed_natural
from .power_mel import (PowerMelScales, log_mel_with_floor, mel_accumulate,
                        power_integer)
from .qnum import (OverflowCounter, OverflowPolicy, RoundMode, quantize_real,
                   resize_signed, round_half_even)

__all__ = [
    "InputQuantConfig",
    "CandidateConfig",
    "FrameResult",
    "choose_bfp_shift",
    "quantize_frame_input",
    "run_frame",
    "CANDIDATES",
]

# FFT input contract, fixed by the reused core (see PROVENANCE.json).
FFT_IN_WIDTH = 16
FFT_IN_FRAC = 15
FFT_TOTAL_SHIFT_S = 9        # log2(512)
NFFT = 512
NUM_BINS = NFFT // 2 + 1

# Input clamp: the FFT review found the only real-input overflows it observed
# came from the asymmetric minimum code -32768, so the input is clamped to a
# symmetric range.  This removed every overflow observed in the two adversarial
# families that were tested; it is not a general proof.
FFT_IN_CLAMP_LO = -32767
FFT_IN_CLAMP_HI = 32767


@dataclass(frozen=True)
class InputQuantConfig:
    """How the float64 windowed frame becomes the signed-16/F15 FFT input."""

    width: int = FFT_IN_WIDTH
    frac_bits: int = FFT_IN_FRAC
    round_mode: str = RoundMode.HALF_EVEN
    overflow_policy: str = OverflowPolicy.SATURATE
    clamp_lo: int = FFT_IN_CLAMP_LO
    clamp_hi: int = FFT_IN_CLAMP_HI


@dataclass(frozen=True)
class CandidateConfig:
    name: str
    mode: str                     # 'fixed' | 'bfp_pre_quant' | 'q15_then_shift'
    fixed_shift_s: int = 0
    shift_min: int = -2
    shift_max: int = 24
    silence_shift_s: int = 0
    target_peak_int: int = 16384  # 0.5 in Q1.15 -> one bit of headroom
    description: str = ""


CANDIDATES = {
    "fixed_alpha_half": CandidateConfig(
        name="fixed_alpha_half", mode="fixed", fixed_shift_s=0,
        description="s = 0 for every frame; FFT input is quantize(u/2)."),
    "bfp_pre_quant": CandidateConfig(
        name="bfp_pre_quant", mode="bfp_pre_quant",
        description="s chosen from the float64 frame peak, then one "
                    "quantization of u * 2**(s-1)."),
    "bfp_pre_quant_t975": CandidateConfig(
        name="bfp_pre_quant_t975", mode="bfp_pre_quant",
        target_peak_int=31949,
        description="secondary observation, not the primary BFP candidate: "
                    "same pre-quantization exponent choice but the target "
                    "peak is 0.975 of full scale instead of 0.5. The FFT "
                    "reuse review saw no overflow at peak 0.975 in the two "
                    "adversarial families it tested; that is not a general "
                    "guarantee, so overflow must be watched."),
    "q15_then_shift": CandidateConfig(
        name="q15_then_shift", mode="q15_then_shift",
        description="auxiliary: quantize at alpha = 1, then shift integers "
                    "left by s. Information lost at the first quantization "
                    "is not recovered."),
}


@dataclass
class FrameResult:
    shift_s: int
    fft_in: list
    fft_re: list
    fft_im: list
    fft_overflow: bool
    psum: list
    mel_int: list
    log_mel: list
    mel_floored: list
    scales: PowerMelScales
    clip: "ClipReport" = field(default_factory=lambda: ClipReport())
    shift_clamped: bool = False
    counter: OverflowCounter = field(default_factory=OverflowCounter)


# Which inequality decides the frame exponent.  Review fixed_review_20261004
# found that the code and the prose disagreed: the prose said
# round(peak * scale) <= T, the code tested the UNROUNDED peak * scale <= T.
#
# Decision: keep the code's rule and correct the prose.  Reasons:
#   * It is the conservative one.  Since round(x) <= x + 1/2, the unrounded
#     test never selects a LARGER exponent than the rounded test, so it can
#     only reduce overflow headroom risk, never increase it.
#   * It keeps the frozen pilot_01 / pilot_02_target_variant results valid;
#     switching would silently invalidate them.
#   * The cost is at most one exponent step, and only for frames whose peak
#     lands in the half-LSB band just above the target.
# The rounded rule is available as SHIFT_RULE_ROUNDED for comparison; a run
# that uses it is a SEPARATE candidate, never a re-labelling of these results.
SHIFT_RULE_UNROUNDED = "unrounded_peak_times_scale_le_target"
SHIFT_RULE_ROUNDED = "rounded_peak_times_scale_le_target"
SHIFT_RULE_DEFAULT = SHIFT_RULE_UNROUNDED


def choose_bfp_shift(frame, cfg: CandidateConfig, quant: InputQuantConfig,
                     rule: str = SHIFT_RULE_DEFAULT) -> tuple[int, bool]:
    """Largest s in [shift_min, shift_max] whose scaled peak fits the target.

    Contract (``SHIFT_RULE_UNROUNDED``, the default and the rule used for the
    frozen results)::

        s = max { s in [shift_min, shift_max] :
                  peak * 2**(s-1) * 2**frac_bits  <=  target_peak_int }

    evaluated exactly with Fraction on the float64 samples, i.e. the exponent
    is chosen on the wide value *before* any quantization.  The comparison
    uses the UNROUNDED product.

    ``SHIFT_RULE_ROUNDED`` instead compares
    ``round_half_even(peak * 2**(s-1) * 2**frac_bits) <= target_peak_int``.
    It can pick one step higher for a peak in the half-LSB band above the
    target.

    Silence (peak == 0) returns ``cfg.silence_shift_s``.
    Returns ``(s, clamped)``; ``clamped`` is True when the search hit a
    configured bound instead of the target.
    """
    if rule not in (SHIFT_RULE_UNROUNDED, SHIFT_RULE_ROUNDED):
        raise ValueError(f"unknown shift rule {rule!r}")
    peak = max((abs(Fraction(v)) for v in frame), default=Fraction(0))
    if peak == 0:
        return cfg.silence_shift_s, False

    target = Fraction(cfg.target_peak_int)
    unit = Fraction(1 << quant.frac_bits, 1)
    best = None
    for s in range(cfg.shift_max, cfg.shift_min - 1, -1):
        if s >= 1:
            scale = Fraction(1 << (s - 1), 1)
        else:
            scale = Fraction(1, 1 << (1 - s))
        scaled = peak * scale * unit
        if rule == SHIFT_RULE_ROUNDED:
            scaled = Fraction(round_half_even(scaled.numerator,
                                              scaled.denominator))
        if scaled <= target:
            best = s
            break
    if best is None:
        # Even shift_min overflows the target; the quantizer will saturate.
        return cfg.shift_min, True
    return best, best == cfg.shift_max


@dataclass
class ClipReport:
    """Separates *how many samples were clipped* from *how many clip events*.

    Fix for the double count found in review (fixed_review_20261004): a
    negative value that saturates to -32768 and is then pulled to -32767 by
    the symmetric clamp is ONE clipped sample but TWO stage events.  The old
    code returned a single integer that mixed the two, so candidate D's
    reported 1,385 was an event count, not a sample count.  Independently
    recounted: 1,385 events over 923 distinct frame-samples.

    Counting unit: **frame-sample**.  Frames overlap (512 long, hop 160), so
    one PCM position appears in several frames and is counted once per frame.
    These are deliberately separate frame-samples, not duplicates: each frame
    quantizes that position again, with its own exponent.
    """

    clipped_frame_samples: int = 0
    events: dict = field(default_factory=dict)

    def note(self, site: str) -> None:
        self.events[site] = self.events.get(site, 0) + 1

    @property
    def total_events(self) -> int:
        return sum(self.events.values())

    def merge(self, other: "ClipReport") -> None:
        self.clipped_frame_samples += other.clipped_frame_samples
        for k, v in other.events.items():
            self.events[k] = self.events.get(k, 0) + v

    def as_dict(self) -> dict:
        return {
            "clipped_frame_samples": self.clipped_frame_samples,
            "clip_events_total": self.total_events,
            "clip_events_by_stage": dict(sorted(self.events.items())),
            "counting_unit": "frame-sample; overlapping frames count the same "
                             "PCM position once per frame",
        }


def _apply_symmetric_clamp(value: int, quant: InputQuantConfig,
                           report: ClipReport, site: str) -> tuple[int, bool]:
    """Pull value into [clamp_lo, clamp_hi].  Returns (value, fired)."""
    if value < quant.clamp_lo:
        report.note(site)
        return quant.clamp_lo, True
    if value > quant.clamp_hi:
        report.note(site)
        return quant.clamp_hi, True
    return value, False


def quantize_frame_input(frame, shift_s: int, quant: InputQuantConfig,
                         counter: OverflowCounter | None = None
                         ) -> tuple[list[int], ClipReport]:
    """Quantize u * 2**(shift_s - 1) to signed width/frac, then clamp.

    A single quantization: the scale is applied inside quantize_real on the
    exact value, not by shifting an already-quantized integer.

    Returns the integers and a :class:`ClipReport`.  A sample that trips both
    the width policy and the symmetric clamp counts as one clipped sample and
    two events.
    """
    out = []
    report = ClipReport()
    for v in frame:
        clipped_here = False
        q, fired = quantize_real(
            v, quant.frac_bits, quant.width, mode=quant.round_mode,
            policy=quant.overflow_policy, site="fft_in.quantize",
            counter=counter, extra_scale_log2=shift_s - 1)
        if fired:
            report.note("quantize_width_policy")
            clipped_here = True
        q, fired = _apply_symmetric_clamp(q, quant, report,
                                          "symmetric_clamp")
        clipped_here = clipped_here or fired
        if clipped_here:
            report.clipped_frame_samples += 1
        out.append(q)
    return out, report


def _q15_then_shift(frame, shift_s: int, quant: InputQuantConfig,
                    counter: OverflowCounter | None = None
                    ) -> tuple[list[int], ClipReport]:
    """Auxiliary path: quantize at alpha = 1 (s = 1), then integer-shift left."""
    report = ClipReport()
    extra = shift_s - 1
    out = []
    for v in frame:
        clipped_here = False
        q, fired = quantize_real(
            v, quant.frac_bits, quant.width, mode=quant.round_mode,
            policy=quant.overflow_policy, site="fft_in.quantize",
            counter=counter, extra_scale_log2=0)
        if fired:
            report.note("quantize_width_policy")
            clipped_here = True
        q, fired = _apply_symmetric_clamp(q, quant, report, "symmetric_clamp")
        clipped_here = clipped_here or fired

        v2 = (q << extra) if extra >= 0 else (q >> -extra)
        v2, fired = resize_signed(v2, quant.width,
                                  policy=OverflowPolicy.SATURATE,
                                  site="q15_then_shift.resize",
                                  counter=counter)
        if fired:
            report.note("post_shift_width_policy")
            clipped_here = True
        v2, fired = _apply_symmetric_clamp(v2, quant, report,
                                           "post_shift_symmetric_clamp")
        clipped_here = clipped_here or fired

        if clipped_here:
            report.clipped_frame_samples += 1
        out.append(v2)
    return out, report


def run_frame(frame, cfg: CandidateConfig, table: MelTable,
              twiddle, quant: InputQuantConfig | None = None,
              log_floor: float = 1e-12) -> FrameResult:
    """One frame through the mixed-precision path."""
    quant = quant or InputQuantConfig()
    counter = OverflowCounter()
    clamped = False

    if cfg.mode == "fixed":
        s = cfg.fixed_shift_s
    elif cfg.mode in ("bfp_pre_quant", "q15_then_shift"):
        s, clamped = choose_bfp_shift(frame, cfg, quant)
    else:
        raise ValueError(f"unknown candidate mode {cfg.mode!r}")

    if cfg.mode == "q15_then_shift":
        fft_in, clip = _q15_then_shift(frame, s, quant, counter)
    else:
        fft_in, clip = quantize_frame_input(frame, s, quant, counter)

    tw_re, tw_im = twiddle
    re_nat, im_nat, ovf = fft_fixed_natural(fft_in, [0] * len(fft_in),
                                            tw_re, tw_im)
    counter.note("fft.frame_overflow", bool(ovf))

    psum = power_integer(re_nat[:NUM_BINS], im_nat[:NUM_BINS],
                         counter=counter)
    mel_int = mel_accumulate(psum, table, counter=counter)
    scales = PowerMelScales(nfft=NFFT, total_fft_shift_S=FFT_TOTAL_SHIFT_S,
                            bfp_shift_s=s, mel_frac_bits_Fw=table.frac_bits)
    log_mel, floored = log_mel_with_floor(mel_int, scales.mel_exp_log2,
                                          log_floor)
    return FrameResult(shift_s=s, fft_in=fft_in, fft_re=re_nat[:NUM_BINS],
                       fft_im=im_nat[:NUM_BINS], fft_overflow=bool(ovf),
                       psum=psum, mel_int=mel_int, log_mel=log_mel,
                       mel_floored=floored, scales=scales,
                       clip=clip, shift_clamped=clamped,
                       counter=counter)


def dct_ortho(log_mel_frames, dct_matrix):
    """float64 orthonormal DCT-II, 26 -> 13.  Mixed-precision stage (Q2)."""
    import numpy as np
    return np.asarray(log_mel_frames, dtype=np.float64) @ \
        np.asarray(dct_matrix, dtype=np.float64).T


def power_real(psum_frames, power_exp_log2_per_frame):
    """Turn integer psum into common-unit power using each frame's exponent."""
    import numpy as np
    out = np.empty((len(psum_frames), NUM_BINS), dtype=np.float64)
    for i, (row, e) in enumerate(zip(psum_frames, power_exp_log2_per_frame)):
        out[i] = np.asarray(row, dtype=np.float64) * math.ldexp(1.0, e)
    return out


def mel_real(mel_frames, mel_exp_log2_per_frame):
    import numpy as np
    out = np.empty((len(mel_frames), len(mel_frames[0])), dtype=np.float64)
    for i, (row, e) in enumerate(zip(mel_frames, mel_exp_log2_per_frame)):
        out[i] = np.asarray(row, dtype=np.float64) * math.ldexp(1.0, e)
    return out
