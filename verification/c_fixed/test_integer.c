#include "fixed_int.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>

#ifdef CF_TEST_CORE
#include "mfcc_fixed.h"
#include <string.h>
#endif

static uint64_t checks;
static unsigned failures;

static void check(int condition, const char *description)
{
    ++checks;
    if (!condition) {
        if (failures < 20) fprintf(stderr, "FAIL: %s\n", description);
        ++failures;
    }
}

static void known_boundaries(void)
{
    fxi_signed r;
    fxi_unsigned u;
    int64_t out = 71;
    check(fxi_round_even(5, 1).value == 2, "+2.5 -> +2");
    check(fxi_round_even(7, 1).value == 4, "+3.5 -> +4");
    check(fxi_round_even(-5, 1).value == -2, "-2.5 -> -2");
    check(fxi_round_even(-7, 1).value == -4, "-3.5 -> -4");
    check(fxi_round_even(-1, 1).value == 0, "-0.5 -> 0");
    check(fxi_floor_shift(-1, 1).value == -1, "floor differs from nearest");
    check(fxi_round_even(INT64_MIN, 0).value == INT64_MIN, "min identity");
    check(fxi_round_even(INT64_MIN, 1).value == INT64_MIN / 2, "min /2");
    check(fxi_round_even(INT64_MAX, 1).value == INT64_C(4611686018427387904), "max round /2");
    check(fxi_round_even(INT64_MIN, 63).value == -1, "min /2^63");
    check(fxi_round_even(INT64_MAX, 63).value == 1, "max /2^63");
    check(fxi_round_even(INT64_MIN, 64).value == 0, "min /2^64 tie to zero");
    check(fxi_round_even(INT64_MAX, 64).value == 0, "max /2^64");
    check(fxi_floor_shift(INT64_MIN, 64).value == -1, "min floor /2^64");
    check(fxi_floor_shift(INT64_MAX, 64).value == 0, "max floor /2^64");
    check(!fxi_round_even(1, 65).valid, "invalid shift rejected");
    check(!fxi_floor_shift(1, 65).valid, "invalid floor shift rejected");
    check(fxi_sign_extend(UINT64_C(0x8000), 16).value == -32768, "16-bit sign extension");
    check(fxi_sign_extend(UINT64_C(0xFFFF8000), 16).value == -32768, "high bits ignored in decode");
    check(fxi_sign_extend(UINT64_MAX, 64).value == -1, "64-bit negative decode");
    check(fxi_sign_extend(UINT64_C(1) << 63, 64).value == INT64_MIN, "64-bit min decode");
    check(fxi_sign_extend((uint64_t)INT64_MAX, 64).value == INT64_MAX, "64-bit max decode");
    check(fxi_sign_extend(1, 1).value == -1, "one-bit sign extension");
    check(!fxi_sign_extend(1, 0).valid, "zero width rejected");
    check(!fxi_sign_extend(1, 65).valid, "wide width rejected");
    r = fxi_wrap_signed(32768, 16);
    check(r.valid && r.overflow && r.value == -32768, "positive wrap");
    r = fxi_saturate_signed(32768, 16);
    check(r.valid && r.overflow && r.value == 32767, "positive saturation differs");
    r = fxi_wrap_signed(-32769, 16);
    check(r.overflow && r.value == 32767, "negative wrap");
    r = fxi_saturate_signed(-32769, 16);
    check(r.overflow && r.value == -32768, "negative saturation differs");
    check(!fxi_wrap_signed(INT64_MIN, 64).overflow, "64-bit min fits");
    check(!fxi_saturate_signed(INT64_MAX, 64).overflow, "64-bit max fits");
    r = fxi_wrap_signed(INT64_MIN, 63);
    check(r.overflow && r.value == 0, "min64 wrap63");
    r = fxi_saturate_signed(INT64_MIN, 63);
    check(r.overflow && r.value == -INT64_C(4611686018427387904), "min64 saturate63");
    r = fxi_resize_signed(65535, 1, 16, FXI_WRAP);
    check(r.overflow && r.value == -32768, "round-before-overflow positive tie");
    r = fxi_resize_signed(-65537, 1, 16, FXI_WRAP);
    check(!r.overflow && r.value == -32768, "negative tie returns to min");
    r = fxi_resize_signed(-65539, 1, 16, FXI_SATURATE);
    check(r.overflow && r.value == -32768, "negative rounded overflow clamps");
    check(!fxi_resize_signed(1, 0, 16, (fxi_policy)7).valid, "bad signed policy rejected");
    u = fxi_resize_unsigned(UINT64_C(1) << 40, 40, FXI_WRAP);
    check(u.valid && u.overflow && u.value == 0, "unsigned40 wrap");
    u = fxi_resize_unsigned(UINT64_C(1) << 60, 60, FXI_SATURATE);
    check(u.overflow && u.value == (UINT64_C(1) << 60) - 1, "unsigned60 saturation");
    u = fxi_resize_unsigned(UINT64_MAX, 64, FXI_WRAP);
    check(!u.overflow && u.value == UINT64_MAX, "uint64 identity");
    check(!fxi_resize_unsigned(0, 0, FXI_WRAP).valid, "invalid unsigned width");
    check(!fxi_resize_unsigned(0, 1, (fxi_policy)7).valid, "bad unsigned policy");
    check(fxi_add_checked(INT64_MAX, 0, &out) && out == INT64_MAX, "checked max addition");
    out = 71;
    check(!fxi_add_checked(INT64_MAX, 1, &out) && out == 71, "addition overflow leaves output");
    check(!fxi_add_checked(INT64_MIN, -1, &out) && out == 71, "addition underflow leaves output");
    check(fxi_add_checked(INT64_MIN, INT64_MAX, &out) && out == -1, "min + max");
    check(fxi_multiply_checked(INT64_MIN, 1, &out) && out == INT64_MIN, "min *1");
    out = 71;
    check(!fxi_multiply_checked(INT64_MIN, -1, &out) && out == 71, "min *-1 overflow");
    check(!fxi_multiply_checked(INT64_MAX, 2, &out), "max *2 overflow");
    check(fxi_multiply_checked(INT64_MIN, 0, &out) && out == 0, "min *0");
    check(fxi_multiply_checked(-3037000499LL, -3037000499LL, &out) &&
          out == INT64_C(9223372030926249001), "large safe product");
    check(!fxi_multiply_checked(INT64_C(3037000500), INT64_C(3037000500), &out), "large product overflow");
    check(fxi_left_shift_checked(-1, 63, &out) && out == INT64_MIN, "negative scale to min");
    out = 71;
    check(!fxi_left_shift_checked(1, 63, &out) && out == 71, "positive shift overflow");
    check(fxi_left_shift_checked(INT64_MIN, 0, &out) && out == INT64_MIN, "min shift zero");
    check(!fxi_left_shift_checked(INT64_MIN, 1, &out), "min shift overflow");
    check(fxi_left_shift_checked(0, 64, &out) && out == 0, "zero shift64");
    check(!fxi_left_shift_checked(1, 64, &out), "nonzero shift64");
    check(!fxi_left_shift_checked(0, 65, &out), "invalid shift65");
    check(!fxi_add_checked(0, 0, NULL), "null add result");
    check(!fxi_multiply_checked(0, 0, NULL), "null multiply result");
    check(!fxi_left_shift_checked(0, 0, NULL), "null shift result");
}

/* Division/remainder oracle deliberately uses a different formulation from
 * fixed_int.c's unsigned-magnitude bit slicing. Exhaust every signed17 code
 * (including all signed16 inputs) and every reduction shift through 17. */
static void exhaustive_rounding(void)
{
    int64_t v;
    unsigned s;
    for (v = -65536; v <= 65535; ++v) {
        for (s = 0; s <= 17; ++s) {
            const int64_t divisor = INT64_C(1) << s; /* positive only */
            const int64_t q = v / divisor;
            const int64_t remainder = v % divisor;
            const int64_t ar = remainder < 0 ? -remainder : remainder;
            int64_t nearest = q;
            const int64_t floor = q - (remainder < 0 ? 1 : 0);
            if (ar * 2 > divisor || (ar * 2 == divisor && q % 2 != 0))
                nearest += v < 0 ? -1 : 1;
            check(fxi_round_even(v, s).value == nearest, "exhaustive signed17 RNE");
            check(fxi_floor_shift(v, s).value == floor, "exhaustive signed17 floor");
        }
    }
}

static void exhaustive_widths(void)
{
    int64_t v;
    unsigned w;
    for (v = -65536; v <= 65535; ++v) {
        for (w = 1; w <= 17; ++w) {
            const int64_t modulus = INT64_C(1) << w;
            const int64_t half = modulus / 2;
            const bool ov = v < -half || v > half - 1;
            int64_t expected = ((v % modulus) + modulus) % modulus;
            const int64_t clamped = v < -half ? -half : (v > half - 1 ? half - 1 : v);
            const fxi_signed wrapped = fxi_wrap_signed(v, w);
            const fxi_signed saturated = fxi_saturate_signed(v, w);
            if (expected >= half) expected -= modulus;
            check(wrapped.valid && wrapped.value == expected && wrapped.overflow == ov,
                  "exhaustive signed17 wrap");
            check(saturated.valid && saturated.value == clamped && saturated.overflow == ov,
                  "exhaustive signed17 saturate");
        }
    }
}

#ifdef CF_TEST_CORE
/* API validation has no dependency on generated/frozen coefficients. Zero
 * tables meet numeric bounds but are NOT identified as the MFCC coefficient
 * contract. The integration harness verifies the actual table hashes. */
static void core_argument_checks(void)
{
    static cf_tables tables;
    static cf_workspace work;
    static cf_result output, without_trace;
    static cf_fft_trace trace;
    static int16_t re[CF_N], im[CF_N];
    cf_context context = { NULL, 0 };
    unsigned m, k, g;
    int32_t s;
    check(cf_validate_tables(NULL) == CF_BAD_ARGUMENT, "null table rejected");
    check(cf_init(NULL, &tables) == CF_BAD_ARGUMENT, "null context rejected");
    check(cf_init(&context, NULL) == CF_BAD_ARGUMENT && context.ready == 0 && context.tables == NULL,
          "failed init clears context");
    check(cf_process(&context, re, im, 0, &work, &output, NULL) == CF_BAD_ARGUMENT,
          "uninitialized context rejected");
    tables.mel[0][0] = 65537;
    check(cf_init(&context, &tables) == CF_BAD_TABLE && context.ready == 0,
          "Mel coefficient above peak rejected");
    tables.mel[0][0] = 0;
    for (k = 0; k < 24; ++k) tables.mel[0][k] = 65536;
    check(cf_init(&context, &tables) == CF_BAD_TABLE, "Mel sum exceeding proven bound rejected");
    memset(&tables, 0, sizeof(tables));
    check(cf_init(&context, &tables) == CF_OK, "numeric table validation");
    check(cf_process(NULL, re, im, 0, &work, &output, NULL) == CF_BAD_ARGUMENT, "null process context");
    check(cf_process(&context, NULL, im, 0, &work, &output, NULL) == CF_BAD_ARGUMENT, "null real input");
    check(cf_process(&context, re, NULL, 0, &work, &output, NULL) == CF_BAD_ARGUMENT, "null imag input");
    check(cf_process(&context, re, im, 0, NULL, &output, NULL) == CF_BAD_ARGUMENT, "null workspace");
    check(cf_process(&context, re, im, 0, &work, NULL, NULL) == CF_BAD_ARGUMENT, "null output");
    check(cf_process(&context, re, im, -3, &work, &output, NULL) == CF_BAD_EXPONENT, "low exponent rejected");
    check(cf_process(&context, re, im, 25, &work, &output, NULL) == CF_BAD_EXPONENT, "high exponent rejected");
    for (s = -2; s <= 24; ++s) {
        memset(&trace, 0xA5, sizeof(trace));
        check(cf_process(&context, re, im, s, &work, &output, &trace) == CF_OK,
              "all supported exponents accepted");
        check(output.bfp_shift == s && output.power_exp2 == -27 - 2*s &&
              output.mel_exp2 == -43 - 2*s, "exact exponent propagation");
        check(output.fft_overflow == 0 && output.power_overflow == 0 && output.mel_overflow == 0,
              "silence status flags");
        for (k = 0; k < CF_N; ++k) {
            check(output.fft_re[k] == 0 && output.fft_im[k] == 0, "all512 silence bins");
            check(trace.promoted[0][k] == 0 && trace.promoted[1][k] == 0, "promotion trace overwrite");
            for (g = 0; g < CF_FFT_GROUPS; ++g)
                check(trace.groups[g][0][k] == 0 && trace.groups[g][1][k] == 0, "group trace overwrite");
        }
        for (k = 0; k < CF_BINS; ++k) check(output.power[k] == 0, "silence power");
        for (m = 0; m < CF_MELS; ++m) check(output.mel[m] == 0, "silence Mel");
        for (g = 0; g < CF_FFT_GROUPS; ++g)
            check(trace.overflow_after_group[g] == 0, "trace sticky flags");
        check(cf_process(&context, re, im, s, &work, &without_trace, NULL) == CF_OK &&
              memcmp(&output, &without_trace, sizeof(output)) == 0, "NULL trace leaves computation identical");
    }
    /* Verify exact promotion of every sign endpoint independently of table
     * identity; multiplication by16 must never shift a signed negative. */
    for (k = 0; k < CF_N; ++k) {
        re[k] = (k % 2 == 0) ? INT16_MIN : INT16_MAX;
        im[k] = (k % 2 == 0) ? INT16_MAX : INT16_MIN;
    }
    check(cf_process(&context, re, im, 0, &work, &output, &trace) == CF_OK, "signed16 endpoint inputs");
    for (k = 0; k < CF_N; ++k)
        check(trace.promoted[0][k] == (int32_t)re[k] * 16 &&
              trace.promoted[1][k] == (int32_t)im[k] * 16, "exact signed promotion endpoints");
}
#endif

int main(void)
{
    known_boundaries();
    exhaustive_rounding();
    exhaustive_widths();
#ifdef CF_TEST_CORE
    core_argument_checks();
#endif
    printf("integer_checks=%" PRIu64 " failures=%u\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
