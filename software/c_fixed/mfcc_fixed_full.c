#include "mfcc_fixed_full.h"
#include "fixed_int.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

#define CF_FULL_READY UINT32_C(0x43465632)
#define CF_FULL_STATE_READY UINT32_C(0x53545632)
#define CF_LN2_Q30 INT64_C(744261118)
#define CF_LOG_FLOOR_Q24 INT32_C(-463571610)

/* Portable range proof, independent of storage-format declarations:
 * |20*pcm-19*previous| <= 1,277,952; multiplying by32768 fits signed37.
 * RNE division20 fits signed32; signed32*unsigned31 window fits signed63.
 * BFP compares abs(signed32)<=2^31, shifted at most8 (<2^40), to31949.
 * Input requantization multiplies at most256 in int64, then clamps to16.
 * Log mantissa <2^32, so each exact unsigned square fits uint64_t.
 * log2 signed37 cannot be multiplied by LN2 in int64. The exact h/l split
 * below uses |h|<=2^30, p<64*LN2 and |h*LN2|<2^60; q and remainders fit64.
 * DCT signed30* signed31 fits signed61; each signed64 sum is checked before
 * adding. For pinned v2 coefficients and actual log outputs its stronger
 * absolute accumulator bound is2538068713882680420 (<2^62).
 * All shifts of negative signed values and all unchecked signed sums with
 * potential overflow are avoided. No compiler-specific int128 is used. */

static int64_t floor_div(int64_t value, int64_t divisor, int64_t *remainder)
{
    int64_t q = value / divisor, r = value % divisor;
    if (r < 0) { --q; r += divisor; }
    *remainder = r;
    return q;
}

static int64_t rne_div20(int64_t value)
{
    int64_t r;
    const int64_t q = floor_div(value, INT64_C(20), &r);
    return q + ((r > 10 || (r == 10 && q % 2 != 0)) ? 1 : 0);
}

cf_full_status cf_full_preemphasis(int16_t current, int16_t previous, int32_t *output)
{
    int64_t value;
    if (output == NULL) return CF_FULL_BAD_ARGUMENT;
    value = rne_div20((INT64_C(20) * current - INT64_C(19) * previous) * INT64_C(32768));
    if (!fxi_fits_signed(value, 32)) return CF_FULL_FRONT_RANGE;
    *output = (int32_t)value;
    return CF_FULL_OK;
}

cf_full_status cf_full_choose_bfp(const int32_t windowed[CF_N],
                                 int32_t *shift, uint32_t *clamped)
{
    uint64_t peak = 0;
    unsigned n;
    int32_t s;
    if (windowed == NULL || shift == NULL || clamped == NULL) return CF_FULL_BAD_ARGUMENT;
    for (n = 0; n < CF_N; ++n) {
        const int64_t value = windowed[n];
        const uint64_t magnitude = (uint64_t)(value < 0 ? -value : value);
        if (magnitude > peak) peak = magnitude;
    }
    if (peak == 0) { *shift = 0; *clamped = 0; return CF_FULL_OK; }
    for (s = 24; s >= -2; --s) {
        const int fits = s >= 16 ?
            ((peak << (unsigned)(s - 16)) <= UINT64_C(31949)) :
            (peak <= (UINT64_C(31949) << (unsigned)(16 - s)));
        if (fits) {
            *shift = s;
            *clamped = s == 24 ? UINT32_C(1) : UINT32_C(0);
            return CF_FULL_OK;
        }
    }
    *shift = -2; *clamped = 1;
    return CF_FULL_OK;
}

cf_full_status cf_full_quantize(const int32_t windowed[CF_N], int32_t shift,
                                int16_t output[CF_N], uint32_t *clips)
{
    unsigned n;
    uint32_t count = 0;
    if (windowed == NULL || output == NULL || clips == NULL) return CF_FULL_BAD_ARGUMENT;
    if (shift < -2 || shift > 24) return CF_FULL_BAD_EXPONENT;
    for (n = 0; n < CF_N; ++n) {
        int64_t value;
        if (shift <= 16) value = fxi_round_even(windowed[n], (unsigned)(16 - shift)).value;
        else value = (int64_t)windowed[n] * (int64_t)(UINT64_C(1) << (unsigned)(shift - 16));
        if (value > 32767) { value = 32767; ++count; }
        else if (value < -32767) { value = -32767; ++count; }
        output[n] = (int16_t)value;
    }
    *clips = count;
    return CF_FULL_OK;
}

static int valid_exponent(int32_t exponent)
{
    return exponent >= -91 && exponent <= -39 && (exponent + 91) % 2 == 0;
}

cf_full_status cf_full_floor(uint64_t mel, int32_t exponent, uint32_t *floored)
{
    uint64_t threshold;
    unsigned power;
    if (floored == NULL) return CF_FULL_BAD_ARGUMENT;
    if (!fxi_fits_unsigned(mel, 60)) return CF_FULL_LOG_RANGE;
    if (!valid_exponent(exponent)) return CF_FULL_BAD_EXPONENT;
    /* floor(2^(-exponent)/10^12) = floor(2^(-exponent-12)/244140625).
     * The latter power is27..79. For powers>=64 split into base2^32 digits:
     * high<=2^47, remainder<244140625, remainder*2^32<2^60.
     * Final threshold<=2475880078570760. No oversized product/shift occurs.
     * Contract proves this decimal-rational boundary equals the canonical
     * binary64 floor on all27 supported grids; equality is impossible. */
    power = (unsigned)(-exponent - 12);
    if (power < 64) threshold = (UINT64_C(1) << power) / UINT64_C(244140625);
    else {
        const uint64_t high = UINT64_C(1) << (power - 32);
        const uint64_t quotient = high / UINT64_C(244140625);
        const uint64_t remainder = high % UINT64_C(244140625);
        threshold = (quotient << 32) + ((remainder << 32) / UINT64_C(244140625));
    }
    *floored = mel <= threshold ? UINT32_C(1) : UINT32_C(0);
    return CF_FULL_OK;
}

cf_full_status cf_full_log_scale(int64_t log2_q30, int32_t *ln_q24)
{
    int64_t l, r, h, p, b, q;
    if (ln_q24 == NULL) return CF_FULL_BAD_ARGUMENT;
    if (!fxi_fits_signed(log2_q30, 37)) return CF_FULL_LOG_RANGE;
    h = floor_div(log2_q30, INT64_C(64), &l);
    p = l * CF_LN2_Q30;
    b = h * CF_LN2_Q30 + p / INT64_C(64);
    q = floor_div(b, INT64_C(1073741824), &r);
    /* Low6 product bits distinguish a value just ABOVE a tie from a tie.
     * Testing r alone or separately rounding h/l would be incorrect. */
    if (r > INT64_C(536870912) ||
        (r == INT64_C(536870912) && (p % 64 != 0 || q % 2 != 0))) ++q;
    if (!fxi_fits_signed(q, 32)) return CF_FULL_LOG_RANGE;
    *ln_q24 = (int32_t)q;
    return CF_FULL_OK;
}

cf_full_status cf_full_log(uint64_t mel, int32_t exponent,
                           int32_t *log_q24, uint32_t *floored)
{
    cf_full_status status;
    uint64_t mantissa, probe;
    uint32_t fraction = 0, floor_flag;
    unsigned lead = 0, i;
    int64_t log2_q30;
    int32_t value;
    if (log_q24 == NULL || floored == NULL) return CF_FULL_BAD_ARGUMENT;
    status = cf_full_floor(mel, exponent, &floor_flag);
    if (status != CF_FULL_OK) return status;
    if (floor_flag != 0) { *log_q24 = CF_LOG_FLOOR_Q24; *floored = 1; return CF_FULL_OK; }
    for (probe = mel; probe > 1; probe >>= 1) ++lead;
    mantissa = lead >= 31 ? mel >> (lead - 31) : mel << (31 - lead);
    for (i = 0; i < 30; ++i) {
        const uint64_t square = (mantissa * mantissa) >> 31;
        const unsigned bit = square >= (UINT64_C(1) << 32) ? 1U : 0U;
        mantissa = square >> bit;
        fraction = (fraction << 1) | bit;
    }
    log2_q30 = ((int64_t)lead + exponent) * INT64_C(1073741824) + fraction;
    status = cf_full_log_scale(log2_q30, &value);
    if (status != CF_FULL_OK || !fxi_fits_signed(value, 30)) return CF_FULL_LOG_RANGE;
    *log_q24 = value; *floored = 0;
    return CF_FULL_OK;
}

cf_full_status cf_full_dct(const cf_full_tables *tables,
                           const int32_t log_q24[CF_MELS], int64_t output[CF_FULL_COEFFICIENTS])
{
    unsigned c, m;
    if (tables == NULL || log_q24 == NULL || output == NULL) return CF_FULL_BAD_ARGUMENT;
    for (c = 0; c < CF_FULL_COEFFICIENTS; ++c) {
        int64_t sum = 0;
        fxi_signed rounded;
        for (m = 0; m < CF_MELS; ++m) {
            int64_t product;
            if (!fxi_fits_signed(log_q24[m], 30) || !fxi_fits_signed(tables->dct_q30[c][m], 31))
                return CF_FULL_DCT_RANGE;
            product = (int64_t)log_q24[m] * (int64_t)tables->dct_q30[c][m];
            if (!fxi_fits_signed(product, 61) || !fxi_add_checked(sum, product, &sum))
                return CF_FULL_DCT_RANGE;
        }
        rounded = fxi_round_even(sum, 30);
        if (!fxi_fits_signed(rounded.value, 40)) return CF_FULL_DCT_RANGE;
        output[c] = rounded.value;
    }
    return CF_FULL_OK;
}

cf_full_status cf_full_init(cf_full_context *context, const cf_tables *spectral,
                            const cf_full_tables *tables)
{
    unsigned n, c, m;
    if (context == NULL) return CF_FULL_BAD_ARGUMENT;
    memset(context, 0, sizeof(*context));
    if (tables == NULL || spectral == NULL) return CF_FULL_BAD_ARGUMENT;
    if (cf_init(&context->spectral, spectral) != CF_OK) return CF_FULL_BAD_TABLE;
    for (n = 0; n < CF_N; ++n)
        if (!fxi_fits_unsigned(tables->window_q30[n], 31)) return CF_FULL_BAD_TABLE;
    for (c = 0; c < CF_FULL_COEFFICIENTS; ++c)
        for (m = 0; m < CF_MELS; ++m)
            if (!fxi_fits_signed(tables->dct_q30[c][m], 31)) return CF_FULL_BAD_TABLE;
    context->tables = tables;
    context->ready = CF_FULL_READY;
    return CF_FULL_OK;
}

cf_full_status cf_full_reset(cf_full_state *state)
{
    if (state == NULL) return CF_FULL_BAD_ARGUMENT;
    memset(state, 0, sizeof(*state));
    state->samples_until_frame = CF_N;
    state->ready = CF_FULL_STATE_READY;
    return CF_FULL_OK;
}

static cf_full_status process_frame(const cf_full_context *context, cf_full_state *state,
                                    cf_full_workspace *work, cf_full_output *output)
{
    unsigned n, m;
    int32_t shift;
    cf_full_status status;
    memset(output, 0, sizeof(*output));
    output->frame_id = state->frames_emitted;
    output->start_sample = state->samples_seen - CF_N;
    for (n = 0; n < CF_N; ++n) {
        const unsigned ring = (state->write_index + n) % CF_N;
        const int64_t product = (int64_t)state->pre_ring[ring] * context->tables->window_q30[n];
        const fxi_signed windowed = fxi_round_even(product, 30);
        if (!fxi_fits_signed(windowed.value, 32)) return CF_FULL_FRONT_RANGE;
        output->windowed_q30[n] = (int32_t)windowed.value;
    }
    status = cf_full_choose_bfp(output->windowed_q30, &shift, &output->bfp_clamped);
    if (status != CF_FULL_OK) return status;
    status = cf_full_quantize(output->windowed_q30, shift, output->fft_input, &output->input_clips);
    if (status != CF_FULL_OK) return status;
    memset(work->imag_zero, 0, sizeof(work->imag_zero));
    if (cf_process(&context->spectral, output->fft_input, work->imag_zero, shift,
                   &work->spectral, &output->spectral, NULL) != CF_OK) return CF_FULL_SPECTRAL_ERROR;
    for (m = 0; m < CF_MELS; ++m) {
        status = cf_full_log(output->spectral.mel[m], output->spectral.mel_exp2,
                             &output->log_q24[m], &output->floor[m]);
        if (status != CF_FULL_OK) return status;
    }
    return cf_full_dct(context->tables, output->log_q24, output->mfcc_q24);
}

cf_full_status cf_full_push(const cf_full_context *context, cf_full_state *state,
                            int16_t pcm, cf_full_workspace *work, cf_full_output *output,
                            uint32_t *produced, int32_t *preemphasis_q30)
{
    cf_full_status status;
    int32_t pre;
    if (produced == NULL) return CF_FULL_BAD_ARGUMENT;
    *produced = 0;
    if (context == NULL || state == NULL || work == NULL || output == NULL ||
        context->ready != CF_FULL_READY || context->tables == NULL) return CF_FULL_BAD_ARGUMENT;
    if (state->ready != CF_FULL_STATE_READY || state->write_index >= CF_N ||
        state->samples_until_frame == 0 || state->samples_until_frame > CF_N)
        return CF_FULL_STATE_ERROR;
    if (state->sticky_status != CF_FULL_OK) return (cf_full_status)state->sticky_status;
    if (state->finished != 0) return CF_FULL_FINISHED;
    if (state->samples_seen == UINT32_MAX) {
        state->sticky_status = CF_FULL_COUNT_RANGE;
        return CF_FULL_COUNT_RANGE;
    }
    status = cf_full_preemphasis(pcm, state->previous_pcm, &pre);
    if (status != CF_FULL_OK) { state->sticky_status = (uint32_t)status; return status; }
    state->previous_pcm = pcm;
    state->pre_ring[state->write_index] = pre;
    ++state->write_index;
    if (state->write_index == CF_N) state->write_index = 0;
    ++state->samples_seen;
    --state->samples_until_frame;
    if (preemphasis_q30 != NULL) *preemphasis_q30 = pre;
    if (state->samples_until_frame != 0) return CF_FULL_OK;
    /* Restore countdown even on arithmetic failure so the sticky error is
     * returned by subsequent calls instead of a misleading state error. */
    state->samples_until_frame = CF_FULL_HOP;
    status = process_frame(context, state, work, output);
    if (status != CF_FULL_OK) { state->sticky_status = (uint32_t)status; return status; }
    ++state->frames_emitted;
    *produced = 1;
    return CF_FULL_OK;
}

cf_full_status cf_full_finish(cf_full_state *state)
{
    if (state == NULL) return CF_FULL_BAD_ARGUMENT;
    if (state->ready != CF_FULL_STATE_READY) return CF_FULL_STATE_ERROR;
    if (state->sticky_status != CF_FULL_OK) return (cf_full_status)state->sticky_status;
    state->finished = 1;
    return CF_FULL_OK;
}
