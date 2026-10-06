"""Mel weight quantization for the integer Power/Mel path.

The float64 filterbank is regenerated from the equations in docs/MFCC_SPEC.md
(HTK mel scale, ``floor((nfft+1)*hz/sample_rate)`` bin edges, triangles with
peak 1 and no area normalization) and is cross-checked against the frozen
reference table before it is used.  The frozen table is read, never written.

The integer table is ``w_int[m][k] = round_half_even(B[m][k] * 2**Fw)``.  Fw is
a *candidate*, not a fixed decision: docs/NEXT_TASK_FIXED_POINT.md
section 4 requires Fw=16 to be treated as a candidate only, so the generator
takes Fw as an argument and :func:`coefficient_error_sweep` measures what each
choice costs on its own.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from fractions import Fraction

from .qnum import (OverflowPolicy, RoundMode, bits_for_unsigned,
                   round_half_even, unsigned_range)

__all__ = [
    "MelTable",
    "hz_to_mel",
    "mel_to_hz",
    "mel_filterbank_float",
    "quantize_mel_filterbank",
    "coefficient_error_sweep",
]


def hz_to_mel(f: float) -> float:
    return 2595.0 * math.log10(1.0 + f / 700.0)


def mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def mel_filterbank_float(nfft: int = 512, sample_rate: int = 16000,
                         num_filters: int = 26, lowfreq: float = 0.0,
                         highfreq: float | None = None
                         ) -> tuple[list[list[float]], list[int]]:
    """float64 filterbank and its integer bin edges, per MFCC_SPEC."""
    if highfreq is None:
        highfreq = sample_rate / 2.0
    lo, hi = hz_to_mel(lowfreq), hz_to_mel(highfreq)
    points = [lo + (hi - lo) * i / (num_filters + 1)
              for i in range(num_filters + 2)]
    edges = [int(math.floor((nfft + 1) * mel_to_hz(p) / sample_rate))
             for p in points]
    nbins = nfft // 2 + 1
    fb = [[0.0] * nbins for _ in range(num_filters)]
    for m in range(num_filters):
        left, centre, right = edges[m], edges[m + 1], edges[m + 2]
        for k in range(left, centre):
            if centre > left:
                fb[m][k] = (k - left) / (centre - left)
        for k in range(centre, right):
            if right > centre:
                fb[m][k] = (right - k) / (right - centre)
    return fb, edges


@dataclass
class MelTable:
    """A quantized Mel filterbank plus everything the RTL contract needs."""

    frac_bits: int
    weights_int: list[list[int]]
    weights_float: list[list[float]]
    edges: list[int]
    num_filters: int
    num_bins: int
    weight_width_unsigned: int
    max_weight_int: int
    per_band_weight_sum_int: list[int]
    per_band_nonzero_bins: list[int]
    max_abs_coefficient_error: float
    rms_coefficient_error: float
    round_mode: str = RoundMode.HALF_EVEN
    overflow_policy: str = OverflowPolicy.ERROR

    def accumulator_bound(self, psum_max: int) -> dict:
        """Exact bounds for T_m = sum_k psum[k] * w_int[m][k]."""
        tight = max(psum_max * s for s in self.per_band_weight_sum_int)
        loose = psum_max * self.num_bins * (1 << self.frac_bits)
        return {
            "psum_max": psum_max,
            "tight_bound_from_this_table": tight,
            "tight_bound_bits_unsigned": bits_for_unsigned(tight),
            "loose_bound_all_bins_weight_one": loose,
            "loose_bound_bits_unsigned": bits_for_unsigned(loose),
            "worst_band_index": max(
                range(self.num_filters),
                key=lambda m: self.per_band_weight_sum_int[m]),
        }

    def manifest(self) -> dict:
        flat = [w for row in self.weights_int for w in row]
        digest = hashlib.sha256(
            ",".join(str(w) for w in flat).encode("ascii")).hexdigest()
        return {
            "generator": "software/fixed_model/coeffs.py:quantize_mel_filterbank",
            "definition": "docs/MFCC_SPEC.md sections 2-3 (htk mel, floor bin rule, peak-one triangles)",
            "frac_bits_Fw": self.frac_bits,
            "status": "candidate (not a confirmed final value)",
            "round_mode": self.round_mode,
            "overflow_policy": self.overflow_policy,
            "num_filters": self.num_filters,
            "num_bins": self.num_bins,
            "weight_width_unsigned": self.weight_width_unsigned,
            "max_weight_int": self.max_weight_int,
            "edges": self.edges,
            "per_band_weight_sum_int": self.per_band_weight_sum_int,
            "per_band_nonzero_bins": self.per_band_nonzero_bins,
            "max_abs_coefficient_error": self.max_abs_coefficient_error,
            "rms_coefficient_error": self.rms_coefficient_error,
            "weights_int_sha256": digest,
        }


def quantize_mel_filterbank(frac_bits: int = 16, nfft: int = 512,
                            sample_rate: int = 16000, num_filters: int = 26,
                            lowfreq: float = 0.0,
                            highfreq: float | None = None) -> MelTable:
    fb, edges = mel_filterbank_float(nfft, sample_rate, num_filters,
                                     lowfreq, highfreq)
    scale = 1 << frac_bits
    w_int: list[list[int]] = []
    err_max = 0.0
    err_sq = 0.0
    count = 0
    for row in fb:
        out_row = []
        for b in row:
            # Exact rounding of b * 2**Fw on the exact binary value of b.
            # Fraction(b) is that exact value, so a tie here is a real tie.
            v = Fraction(b) * scale
            q = round_half_even(v.numerator, v.denominator)
            lo, hi = unsigned_range(frac_bits + 1)
            if not (lo <= q <= hi):
                raise OverflowError(
                    f"mel weight {b} -> {q} does not fit unsigned "
                    f"{frac_bits + 1} bits")
            out_row.append(q)
            e = abs(q / scale - b)
            err_max = max(err_max, e)
            err_sq += e * e
            count += 1
        w_int.append(out_row)
    sums = [sum(r) for r in w_int]
    nz = [sum(1 for v in r if v != 0) for r in w_int]
    return MelTable(
        frac_bits=frac_bits,
        weights_int=w_int,
        weights_float=fb,
        edges=edges,
        num_filters=num_filters,
        num_bins=nfft // 2 + 1,
        weight_width_unsigned=frac_bits + 1,
        max_weight_int=max(max(r) for r in w_int),
        per_band_weight_sum_int=sums,
        per_band_nonzero_bins=nz,
        max_abs_coefficient_error=err_max,
        rms_coefficient_error=math.sqrt(err_sq / count) if count else 0.0,
    )


def coefficient_error_sweep(power_frames, frac_bits_choices=(12, 14, 16, 18, 20),
                            nfft: int = 512, sample_rate: int = 16000,
                            num_filters: int = 26):
    """Isolate Mel *coefficient* quantization error from every other error.

    Both sides use the identical float64 power spectrum, so the only
    difference is the weight table.  This answers "what does Fw cost by
    itself", separately from FFT input/stage quantization.
    """
    import numpy as np

    p = np.asarray(power_frames, dtype=np.float64)
    fb, _ = mel_filterbank_float(nfft, sample_rate, num_filters)
    b_float = np.asarray(fb, dtype=np.float64)
    e_ref = p @ b_float.T

    rows = []
    for fw in frac_bits_choices:
        table = quantize_mel_filterbank(fw, nfft, sample_rate, num_filters)
        b_q = np.asarray(table.weights_int, dtype=np.float64) / float(1 << fw)
        e_q = p @ b_q.T
        denom = np.where(e_ref > 0.0, e_ref, np.nan)
        rel = np.abs(e_q - e_ref) / denom
        with np.errstate(divide="ignore", invalid="ignore"):
            dlog = np.abs(np.log(np.maximum(e_q, 1e-12))
                          - np.log(np.maximum(e_ref, 1e-12)))
        rows.append({
            "frac_bits_Fw": fw,
            "weight_width_unsigned": table.weight_width_unsigned,
            "max_abs_coefficient_error": table.max_abs_coefficient_error,
            "rms_coefficient_error": table.rms_coefficient_error,
            "mel_energy_max_abs_error": float(np.max(np.abs(e_q - e_ref))),
            "mel_energy_max_rel_error": float(np.nanmax(rel)),
            "log_mel_max_abs_error_nats": float(np.max(dlog)),
            "accumulator_bits_tight": table.accumulator_bound(
                2 * 32768 * 32768)["tight_bound_bits_unsigned"],
        })
    return rows


def write_manifest(table: MelTable, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(table.manifest(), fh, indent=2)
        fh.write("\n")
