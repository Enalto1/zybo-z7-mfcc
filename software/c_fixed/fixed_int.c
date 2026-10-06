#include "fixed_int.h"

#include <limits.h>
#include <stddef.h>

static uint64_t magnitude(int64_t value)
{
    return value < 0 ? (uint64_t)(-(value + INT64_C(1))) + UINT64_C(1)
                     : (uint64_t)value;
}

static int64_t from_magnitude(uint64_t mag, bool negative)
{
    if (!negative) return (int64_t)mag; /* caller proves mag <= INT64_MAX */
    if (mag == (UINT64_C(1) << 63)) return INT64_MIN;
    return -(int64_t)mag;
}

static uint64_t width_mask(unsigned width)
{
    return width == 64 ? UINT64_MAX : (UINT64_C(1) << width) - UINT64_C(1);
}

bool fxi_fits_signed(int64_t value, unsigned width)
{
    int64_t half;
    if (width == 0 || width > 64) return false;
    if (width == 64) return true;
    half = (int64_t)(UINT64_C(1) << (width - 1));
    return value >= -half && value <= half - INT64_C(1);
}

bool fxi_fits_unsigned(uint64_t value, unsigned width)
{
    if (width == 0 || width > 64) return false;
    return value <= width_mask(width);
}

fxi_signed fxi_sign_extend(uint64_t bits, unsigned width)
{
    fxi_signed out = { 0, false, false };
    uint64_t code, mask;
    if (width == 0 || width > 64) return out;
    mask = width_mask(width);
    code = bits & mask;
    out.valid = true;
    if ((code & (UINT64_C(1) << (width - 1))) != 0) {
        out.value = from_magnitude(((~code) & mask) + UINT64_C(1), true);
    } else {
        out.value = (int64_t)code;
    }
    return out;
}

fxi_signed fxi_wrap_signed(int64_t value, unsigned width)
{
    fxi_signed out = fxi_sign_extend((uint64_t)value, width);
    if (out.valid) out.overflow = !fxi_fits_signed(value, width);
    return out;
}

fxi_signed fxi_saturate_signed(int64_t value, unsigned width)
{
    fxi_signed out = { 0, false, false };
    int64_t half;
    if (width == 0 || width > 64) return out;
    out.value = value;
    out.valid = true;
    if (fxi_fits_signed(value, width)) return out;
    half = (int64_t)(UINT64_C(1) << (width - 1)); /* width < 64 here */
    out.overflow = true;
    out.value = value < 0 ? -half : half - INT64_C(1);
    return out;
}

fxi_unsigned fxi_resize_unsigned(uint64_t value, unsigned width, fxi_policy policy)
{
    fxi_unsigned out = { 0, false, false };
    uint64_t mask;
    if (width == 0 || width > 64 || (policy != FXI_WRAP && policy != FXI_SATURATE))
        return out;
    mask = width_mask(width);
    out.valid = true;
    out.overflow = value > mask;
    out.value = policy == FXI_WRAP ? value & mask : (out.overflow ? mask : value);
    return out;
}

fxi_signed fxi_round_even(int64_t value, unsigned right_shift)
{
    fxi_signed out = { 0, false, false };
    uint64_t mag, quotient, remainder, half;
    if (right_shift > 64) return out;
    out.valid = true;
    if (right_shift == 0) { out.value = value; return out; }
    /* |int64| <= 2^63; at shift 64 even the endpoint is a tie to zero. */
    if (right_shift == 64) return out;
    mag = magnitude(value);
    quotient = mag >> right_shift;
    remainder = mag & width_mask(right_shift);
    half = UINT64_C(1) << (right_shift - 1);
    if (remainder > half || (remainder == half && (quotient & UINT64_C(1)) != 0))
        ++quotient;
    out.value = from_magnitude(quotient, value < 0);
    return out;
}

fxi_signed fxi_floor_shift(int64_t value, unsigned right_shift)
{
    fxi_signed out = { 0, false, false };
    uint64_t mag, quotient;
    if (right_shift > 64) return out;
    out.valid = true;
    if (right_shift == 0) { out.value = value; return out; }
    if (right_shift == 64) { out.value = value < 0 ? -1 : 0; return out; }
    mag = magnitude(value);
    quotient = mag >> right_shift;
    if (value < 0 && (mag & width_mask(right_shift)) != 0) ++quotient;
    out.value = from_magnitude(quotient, value < 0);
    return out;
}

fxi_signed fxi_resize_signed(int64_t value, unsigned right_shift,
                             unsigned width, fxi_policy policy)
{
    fxi_signed out = fxi_round_even(value, right_shift);
    if (!out.valid) return out;
    if (policy == FXI_WRAP) return fxi_wrap_signed(out.value, width);
    if (policy == FXI_SATURATE) return fxi_saturate_signed(out.value, width);
    out.value = 0; out.valid = false;
    return out;
}

bool fxi_add_checked(int64_t a, int64_t b, int64_t *out)
{
    if (out == NULL || (b > 0 && a > INT64_MAX - b) ||
        (b < 0 && a < INT64_MIN - b)) return false;
    *out = a + b;
    return true;
}

bool fxi_multiply_checked(int64_t a, int64_t b, int64_t *out)
{
    const bool negative = (a < 0) != (b < 0);
    const uint64_t limit = negative ? UINT64_C(1) << 63 : (uint64_t)INT64_MAX;
    const uint64_t ma = magnitude(a), mb = magnitude(b);
    if (out == NULL || (ma != 0 && mb > limit / ma)) return false;
    *out = from_magnitude(ma * mb, negative);
    return true;
}

bool fxi_left_shift_checked(int64_t value, unsigned shift, int64_t *out)
{
    const bool negative = value < 0;
    const uint64_t limit = negative ? UINT64_C(1) << 63 : (uint64_t)INT64_MAX;
    const uint64_t mag = magnitude(value);
    if (out == NULL || shift > 64) return false;
    if (shift == 64) { if (value != 0) return false; *out = 0; return true; }
    if (mag > (limit >> shift)) return false;
    *out = from_magnitude(mag << shift, negative);
    return true;
}
