#ifndef C_FIXED_INT_H
#define C_FIXED_INT_H

#include <stdbool.h>
#include <stdint.h>

/* Portable, explicit two's-complement logical-width operations.
 * Signed widths 1..64 and unsigned widths 1..64 are supported. Invalid
 * widths/shifts return valid=false and value=0, never an unchecked shift.
 * A rounded resize reports overflow after rounding and before wrap/clamp.
 */
typedef struct { int64_t value; bool overflow; bool valid; } fxi_signed;
typedef struct { uint64_t value; bool overflow; bool valid; } fxi_unsigned;
typedef enum { FXI_WRAP = 0, FXI_SATURATE = 1 } fxi_policy;

bool fxi_fits_signed(int64_t value, unsigned width);
bool fxi_fits_unsigned(uint64_t value, unsigned width);
fxi_signed fxi_sign_extend(uint64_t bits, unsigned width);
fxi_signed fxi_wrap_signed(int64_t value, unsigned width);
fxi_signed fxi_saturate_signed(int64_t value, unsigned width);
fxi_unsigned fxi_resize_unsigned(uint64_t value, unsigned width, fxi_policy policy);
fxi_signed fxi_round_even(int64_t value, unsigned right_shift);
fxi_signed fxi_floor_shift(int64_t value, unsigned right_shift);
fxi_signed fxi_resize_signed(int64_t value, unsigned right_shift,
                             unsigned width, fxi_policy policy);
/* Checked operations leave *out unmodified on overflow or invalid arguments. */
bool fxi_add_checked(int64_t a, int64_t b, int64_t *out);
bool fxi_multiply_checked(int64_t a, int64_t b, int64_t *out);
bool fxi_left_shift_checked(int64_t value, unsigned shift, int64_t *out);

#endif
