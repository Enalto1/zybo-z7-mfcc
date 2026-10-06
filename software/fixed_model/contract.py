"""Portable v1 integer FFT20 -> Power40 -> Mel60 contract.

Runtime arithmetic is Python integers. Coefficients are explicit arguments;
their generation is separate. Input Q16/F15 is promoted exactly to Q20/F19.
"""
from __future__ import annotations

from .fft_bitmodel_wide import FftWidthConfig, fft_fixed_natural_wide

FFT_CONFIG = FftWidthConfig(data_width=20, data_frac=19,
                           label="contract_fft20_in16")
NFFT = 512


def spectral_frame(real16, imag16, bfp_s, twiddle, weights):
    if len(real16) != NFFT or len(imag16) != NFFT:
        raise ValueError("exactly 512 complex input samples required")
    if not isinstance(bfp_s, int) or not -2 <= bfp_s <= 24:
        raise ValueError("BFP metadata s must be an integer in [-2,24]")
    for code in list(real16) + list(imag16):
        if isinstance(code, bool) or not isinstance(code, int):
            raise TypeError("FFT inputs must be integer codes")
        if not -32768 <= code <= 32767:
            raise ValueError("FFT input exceeds signed16")
    if len(weights) != 26 or any(len(row) != 257 for row in weights):
        raise ValueError("Mel table shape must be [26,257]")
    if any(not isinstance(w, int) or not 0 <= w <= 65536
           for row in weights for w in row):
        raise ValueError("Mel coefficients must be unsigned17/F16")
    re, im, overflow = fft_fixed_natural_wide(
        [v * 16 for v in real16], [v * 16 for v in imag16],
        *twiddle, cfg=FFT_CONFIG)
    power = [re[k] * re[k] + im[k] * im[k] for k in range(257)]
    mel = [sum(power[k] * row[k] for k in range(257)) for row in weights]
    if any(v < 0 or v >= 1 << 40 for v in power):
        raise OverflowError("Power40 contract violated")
    if any(v < 0 or v >= 1 << 60 for v in mel):
        raise OverflowError("Mel60 contract violated")
    return {"fft_real": re, "fft_imag": im, "power": power, "mel": mel,
            "fft_overflow": bool(overflow), "bfp_s": bfp_s,
            "power_exp2": -27 - 2 * bfp_s,
            "mel_exp2": -43 - 2 * bfp_s}
