"""Version 2 candidate: PCM16 to raw13, integer-only runtime.

Offline coefficient generation belongs to scripts/run_fixed_full_model.py.
No accuracy acceptance threshold has been set. FFT provenance is unchanged.
"""
from __future__ import annotations

from .fft_bitmodel_wide import FftWidthConfig, fft_fixed_natural_wide

FRONT_FRAC = 30
LOG_FRAC = 24
COEFF_FRAC = 30
LN2_Q30 = 744261118
LOG_FLOOR_Q24 = -463571610
FFT_CFG = FftWidthConfig(data_width=20, data_frac=19, label="full_integer_v2")


def rne_div(value: int, divisor: int) -> int:
    """Signed round-nearest-even with positive divisor (including negative ties)."""
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    q, rem = divmod(int(value), int(divisor))
    return q + int(2 * rem > divisor or (2 * rem == divisor and q & 1))


def rne_shift(value: int, shift: int) -> int:
    return rne_div(value, 1 << shift) if shift > 0 else int(value) << (-shift)


def checked_signed(value: int, width: int) -> int:
    if not -(1 << (width - 1)) <= value < (1 << (width - 1)):
        raise OverflowError(f"{value} is not signed{width}")
    return value


def preemphasis_pcm(pcm) -> list[int]:
    """Continuous alpha=19/20. Previous raw PCM sample resets only at clip start."""
    result, prev = [], 0
    for raw in pcm:
        sample = checked_signed(int(raw), 16)
        numerator = 20 * sample - 19 * prev
        result.append(checked_signed(rne_div(numerator << 15, 20), 32))
        prev = sample
    return result


def window_frame(pre_frame, window_q30) -> list[int]:
    if len(pre_frame) != 512 or len(window_q30) != 512:
        raise ValueError("window needs exactly 512 samples and coefficients")
    return [checked_signed(rne_shift(int(x) * int(w), 30), 32)
            for x, w in zip(pre_frame, window_q30)]


def choose_bfp_integer(windowed_q30) -> tuple[int, bool]:
    """Largest s in [-2,24] with peak*2^(s-16)<=31949, before input rounding."""
    peak = max((abs(int(x)) for x in windowed_q30), default=0)
    if peak == 0:
        return 0, False
    for s in range(24, -3, -1):
        fits = (peak << (s - 16)) <= 31949 if s >= 16 else peak <= (31949 << (16 - s))
        if fits:
            return s, s == 24
    return -2, True


def quantize_fft_input(windowed_q30, shift_s: int) -> tuple[list[int], int]:
    codes, clips = [], 0
    for value in windowed_q30:
        rounded = rne_shift(int(value), 16 - shift_s)
        code = min(32767, max(-32767, rounded))
        clips += int(code != rounded)
        codes.append(code)
    return codes, clips


def below_floor(mel: int, exponent: int) -> bool:
    """Integer floor test equivalent to canonical binary64 1e-12 on valid grids.

    For exponent=-43-2*s, s in [-2,24], ceil(floor*2**-exponent)
    agrees for binary64 1e-12 and rational 1/10**12. Neither is exactly
    representable by integer*2**exponent, so <= versus < cannot differ.
    """
    if mel < 0:
        raise ValueError("Mel energy must be unsigned")
    if exponent >= 0:
        return (mel << exponent) * 1_000_000_000_000 <= 1
    return mel * 1_000_000_000_000 <= (1 << -exponent)


def log_integer(mel: int, exponent: int) -> tuple[int, bool]:
    """Natural log to signed30/F24; normalization Q31, 30 binary-log iterations.

    Normalize by truncation, not rounding; every square is unsigned64, >>31.
    If square Q31 >=2, divide by 2 (truncate) and append fractional log2 bit.
    Multiply signed log2 Q30 by LN2 Q30; RNE >>36 produces Q24.
    """
    mel = int(mel)
    if not 0 <= mel < (1 << 60):
        raise OverflowError("Mel must fit unsigned60")
    if exponent not in range(-91, -38, 2):
        raise ValueError("Mel exponent must be -43-2*s for s in [-2,24]")
    if below_floor(mel, exponent):
        return LOG_FLOOR_Q24, True
    lead = mel.bit_length() - 1
    mantissa = mel >> (lead - 31) if lead >= 31 else mel << (31 - lead)
    fraction = 0
    for _ in range(30):
        square = (mantissa * mantissa) >> 31
        bit = int(square >= (1 << 32))
        mantissa = square >> bit
        fraction = (fraction << 1) | bit
    log2_q30 = ((lead + int(exponent)) << 30) + fraction
    return checked_signed(rne_shift(log2_q30 * LN2_Q30, 36), 30), False


def dct_integer(log_q24, dct_q30) -> list[int]:
    """26-term increasing-m signed64 sum, one RNE>>30, output signed40/F24.

    Valid logs are bounded by abs(log_floor_q24)=463571610; actual coefficient
    row sums bound the accumulator to 2538068713882680420 (<2**62).
    """
    if len(log_q24) != 26 or len(dct_q30) != 13:
        raise ValueError("DCT shape must be 26 -> 13")
    output = []
    for row in dct_q30:
        if len(row) != 26:
            raise ValueError("DCT row must have 26 values")
        accum = 0
        for value, coef in zip(log_q24, row):
            product = checked_signed(int(value), 30) * checked_signed(int(coef), 31)
            accum = checked_signed(accum + checked_signed(product, 61), 64)
        output.append(checked_signed(rne_shift(accum, 30), 40))
    return output


def run_pcm(pcm, coefficients: dict, twiddle) -> dict:
    """All runtime arithmetic is integer. Returns full 512 FFT bins per frame."""
    pre = preemphasis_pcm(pcm)
    result = {key: [] for key in ("frame_starts", "windowed_q30", "shift_s", "fft_input", "fft_re", "fft_im", "power_u40", "mel_u60", "mel_exponent", "log_q24", "floor", "mfcc_q24", "fft_overflow", "input_clips", "bfp_clamped")}
    result["preemphasis_q30"] = pre
    for start in range(0, max(0, len(pre) - 511), 160):
        windowed = window_frame(pre[start:start + 512], coefficients["window_q30"])
        shift, clamped = choose_bfp_integer(windowed)
        fft_input, clips = quantize_fft_input(windowed, shift)
        re, im, overflow = fft_fixed_natural_wide([x << 4 for x in fft_input], [0] * 512, *twiddle, FFT_CFG)
        power = [r * r + i * i for r, i in zip(re[:257], im[:257])]
        if any(not 0 <= x < (1 << 40) for x in power):
            raise OverflowError("Power unsigned40 overflow")
        mel = [sum(p * int(w) for p, w in zip(power, row)) for row in coefficients["mel_q16"]]
        if any(not 0 <= x < (1 << 60) for x in mel):
            raise OverflowError("Mel unsigned60 overflow")
        exponent = -43 - 2 * shift
        log_floor = [log_integer(t, exponent) for t in mel]
        logs, floors = [x[0] for x in log_floor], [x[1] for x in log_floor]
        values = (start, windowed, shift, fft_input, re, im, power, mel, exponent, logs, floors, dct_integer(logs, coefficients["dct_q30"]), overflow, clips, clamped)
        for key, value in zip(tuple(result)[:-1], values):
            result[key].append(value)
    return result
