#include "mfcc_fixed.h"
#include "fixed_int.h"

#include <stddef.h>
#include <string.h>

#define CF_READY UINT32_C(0x43463230)
#define CF_WEIGHT_MAX UINT32_C(65536)
#define CF_WEIGHT_SUM_MAX UINT64_C(1540096)

/* Range proof for the only supported profile:
 * promotion: signed16 *16 -> [-524288,524272], signed20/F19.
 * Type I: signed20 +/- signed20 -> signed21.
 * Type II: signed21 +/- signed21, including exact -j rotation -> signed22.
 * A signed22 * signed16 full product fits signed38; complex sum fits signed39
 * (absolute bound <= 2^37). int64_t therefore holds every full product/sum.
 * RNE >>15 then wrap22; RNE >>2 then wrap20. No intervening truncation.
 * trailing sum/difference wrap21 then RNE >>1/wrap20.
 * Power: 2*(2^19)^2 = 2^39, requiring unsigned40.
 * Mel: weights <=65536; product <=2^55; sum <=2^39*1540096 <2^60.
 * No signed operation in this file exceeds these proven int64_t limits.
 */

static int32_t wrap(int64_t value, unsigned width, uint32_t *overflow)
{
    const fxi_signed r = fxi_wrap_signed(value, width);
    *overflow |= r.overflow ? UINT32_C(1) : UINT32_C(0);
    return (int32_t)r.value; /* all call sites request width <=22 */
}

static int32_t round_wrap(int64_t value, unsigned shift, unsigned width,
                          uint32_t *overflow)
{
    const fxi_signed r = fxi_resize_signed(value, shift, width, FXI_WRAP);
    *overflow |= r.overflow ? UINT32_C(1) : UINT32_C(0);
    return (int32_t)r.value;
}

static unsigned reverse9(unsigned value)
{
    unsigned result = 0, b;
    for (b = 0; b < 9; ++b) {
        result = (result << 1) | (value & 1U);
        value >>= 1;
    }
    return result;
}

cf_status cf_validate_tables(const cf_tables *tables)
{
    unsigned m, k;
    if (tables == NULL) return CF_BAD_ARGUMENT;
    /* int16_t ROM members already enforce the signed16 coefficient domain.
     * Table identity is verified by the host manifest, not recomputed here. */
    for (m = 0; m < CF_MELS; ++m) {
        uint64_t sum = 0;
        for (k = 0; k < CF_BINS; ++k) {
            const uint32_t w = tables->mel[m][k];
            if (w > CF_WEIGHT_MAX) return CF_BAD_TABLE;
            sum += (uint64_t)w; /* <=257*65536 */
        }
        if (sum > CF_WEIGHT_SUM_MAX) return CF_BAD_TABLE;
    }
    return CF_OK;
}

cf_status cf_init(cf_context *context, const cf_tables *tables)
{
    cf_status status;
    if (context == NULL) return CF_BAD_ARGUMENT;
    context->tables = NULL;
    context->ready = 0;
    status = cf_validate_tables(tables);
    if (status != CF_OK) return status;
    context->tables = tables;
    context->ready = CF_READY;
    return CF_OK;
}

static void group(const cf_tables *tables, cf_workspace *work, unsigned length,
                   uint32_t *overflow)
{
    static const unsigned branch[4] = { 0, 2, 1, 3 };
    const unsigned half = length / 2, quarter = length / 4;
    const unsigned rom_stride = CF_ROM / length;
    unsigned base, n, component, j;

    for (base = 0; base < CF_N; base += length) {
        /* Type I preserves F19 and adds the first guard bit. */
        for (component = 0; component < 2; ++component) {
            for (n = 0; n < half; ++n) {
                const int64_t a = work->current[component][base + n];
                const int64_t b = work->current[component][base + half + n];
                work->scratch[component][base + n] = wrap(a + b, 21, overflow);
                work->scratch[component][base + half + n] = wrap(a - b, 21, overflow);
            }
        }
        /* Type II: first half ordinary butterflies, second half uses -j.
         * Rotation is performed in int64_t before the signed22 boundary. */
        for (n = 0; n < quarter; ++n) {
            for (component = 0; component < 2; ++component) {
                const int64_t a = work->scratch[component][base + n];
                const int64_t b = work->scratch[component][base + quarter + n];
                work->current[component][base + n] = wrap(a + b, 22, overflow);
                work->current[component][base + quarter + n] = wrap(a - b, 22, overflow);
            }
            {
                const int64_t dr = work->scratch[0][base + 2 * quarter + n];
                const int64_t di = work->scratch[1][base + 2 * quarter + n];
                const int64_t xr = work->scratch[0][base + 3 * quarter + n];
                const int64_t xi = work->scratch[1][base + 3 * quarter + n];
                work->current[0][base + 2 * quarter + n] = wrap(dr + xi, 22, overflow);
                work->current[1][base + 2 * quarter + n] = wrap(di - xr, 22, overflow);
                work->current[0][base + 3 * quarter + n] = wrap(dr - xi, 22, overflow);
                work->current[1][base + 3 * quarter + n] = wrap(di + xr, 22, overflow);
            }
        }
        /* Frozen model quarter branch order is (0,2,1,3). In particular,
         * do not bypass the +1 twiddle (32767/F15) on the zero branch. */
        for (j = 0; j < 4; ++j) {
            for (n = 0; n < quarter; ++n) {
                const unsigned index = base + j * quarter + n;
                const unsigned address = (branch[j] * n * rom_stride) & 1023U;
                const int64_t zr = work->current[0][index];
                const int64_t zi = work->current[1][index];
                const int64_t wr = tables->twiddle_re[address];
                const int64_t wi = tables->twiddle_im[address];
                const int32_t mr = round_wrap(zr * wr - zi * wi, 15, 22, overflow);
                const int32_t mi = round_wrap(zr * wi + zi * wr, 15, 22, overflow);
                work->current[0][index] = round_wrap(mr, 2, 20, overflow);
                work->current[1][index] = round_wrap(mi, 2, 20, overflow);
            }
        }
    }
}

cf_status cf_process(const cf_context *context,
                     const int16_t input_re[CF_N], const int16_t input_im[CF_N],
                     int32_t bfp_shift, cf_workspace *work,
                     cf_result *output, cf_fft_trace *trace)
{
    unsigned n, length, stage = 0, component, m, k;
    const cf_tables *tables;
    if (context == NULL || input_re == NULL || input_im == NULL ||
        work == NULL || output == NULL || context->ready != CF_READY ||
        context->tables == NULL) return CF_BAD_ARGUMENT;
    if (bfp_shift < -2 || bfp_shift > 24) return CF_BAD_EXPONENT;
    tables = context->tables;
    memset(output, 0, sizeof(*output));
    output->bfp_shift = bfp_shift;
    output->power_exp2 = -27 - 2 * bfp_shift;
    output->mel_exp2 = -43 - 2 * bfp_shift;

    for (n = 0; n < CF_N; ++n) {
        /* Multiplication is exact and defined for negative inputs; signed
         * negative left shift is intentionally never used. */
        work->current[0][n] = (int32_t)input_re[n] * INT32_C(16);
        work->current[1][n] = (int32_t)input_im[n] * INT32_C(16);
    }
    if (trace != NULL) memcpy(trace->promoted, work->current, sizeof(trace->promoted));
    for (length = CF_N; length >= 4; length /= 4) {
        group(tables, work, length, &output->fft_overflow);
        if (trace != NULL) {
            memcpy(trace->groups[stage], work->current, sizeof(trace->groups[stage]));
            trace->overflow_after_group[stage] = output->fft_overflow;
        }
        ++stage;
    }
    /* N512 has four complete groups and one trailing Type I, S=4*2+1. */
    for (component = 0; component < 2; ++component) {
        for (n = 0; n < CF_N; n += 2) {
            const int64_t a = work->current[component][n];
            const int64_t b = work->current[component][n + 1];
            const int32_t sum = wrap(a + b, 21, &output->fft_overflow);
            const int32_t diff = wrap(a - b, 21, &output->fft_overflow);
            work->current[component][n] = round_wrap(sum, 1, 20, &output->fft_overflow);
            work->current[component][n + 1] = round_wrap(diff, 1, 20, &output->fft_overflow);
        }
    }
    if (trace != NULL) {
        memcpy(trace->groups[4], work->current, sizeof(trace->groups[4]));
        trace->overflow_after_group[4] = output->fft_overflow;
    }
    for (n = 0; n < CF_N; ++n) {
        const unsigned bin = reverse9(n);
        output->fft_re[bin] = work->current[0][n];
        output->fft_im[bin] = work->current[1][n];
    }
    for (k = 0; k < CF_BINS; ++k) {
        const int64_t re = output->fft_re[k], im = output->fft_im[k];
        uint64_t psum;
        if (!fxi_fits_signed(re, CF_DATA_W) || !fxi_fits_signed(im, CF_DATA_W)) {
            output->power_overflow = 1;
            return CF_POWER_RANGE;
        }
        psum = (uint64_t)(re * re) + (uint64_t)(im * im);
        if (!fxi_fits_unsigned(psum, CF_POWER_W)) {
            output->power_overflow = 1;
            return CF_POWER_RANGE;
        }
        output->power[k] = psum;
    }
    for (m = 0; m < CF_MELS; ++m) {
        uint64_t sum = 0;
        for (k = 0; k < CF_BINS; ++k) {
            const uint32_t weight = tables->mel[m][k];
            if (weight != 0) sum += output->power[k] * (uint64_t)weight;
        }
        if (!fxi_fits_unsigned(sum, CF_MEL_W)) {
            output->mel_overflow = 1;
            return CF_MEL_RANGE;
        }
        output->mel[m] = sum;
    }
    return CF_OK;
}
