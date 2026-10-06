#ifndef C_FIXED_MFCC_FIXED_FULL_H
#define C_FIXED_MFCC_FIXED_FULL_H

#include "mfcc_fixed.h"

/* Published v2 PCM16 -> raw13 arithmetic. Exact executable contract/version
 * and coefficient hashes are supplied by generated host/ARM manifests. */
enum { CF_FULL_HOP = 160, CF_FULL_COEFFICIENTS = 13, CF_FULL_OUTPUT_F = 24 };
typedef enum {
    CF_FULL_OK = 0, CF_FULL_BAD_ARGUMENT = 1, CF_FULL_BAD_TABLE = 2,
    CF_FULL_STATE_ERROR = 3, CF_FULL_COUNT_RANGE = 4, CF_FULL_FRONT_RANGE = 5,
    CF_FULL_BAD_EXPONENT = 6, CF_FULL_LOG_RANGE = 7, CF_FULL_DCT_RANGE = 8,
    CF_FULL_SPECTRAL_ERROR = 9, CF_FULL_FINISHED = 10
} cf_full_status;

typedef struct {
    uint32_t window_q30[CF_N];
    int32_t dct_q30[CF_FULL_COEFFICIENTS][CF_MELS];
} cf_full_tables;

typedef struct {
    cf_context spectral;
    const cf_full_tables *tables;
    uint32_t ready;
} cf_full_context;

typedef struct {
    int32_t pre_ring[CF_N];
    int16_t previous_pcm;
    uint32_t samples_seen;
    uint32_t frames_emitted;
    uint32_t write_index;
    uint32_t samples_until_frame;
    uint32_t sticky_status;
    uint32_t finished;
    uint32_t ready;
} cf_full_state;

typedef struct {
    cf_workspace spectral;
    int16_t imag_zero[CF_N];
} cf_full_workspace;

typedef struct {
    cf_result spectral;
    int32_t windowed_q30[CF_N];
    int16_t fft_input[CF_N];
    int32_t log_q24[CF_MELS];
    uint32_t floor[CF_MELS];
    int64_t mfcc_q24[CF_FULL_COEFFICIENTS]; /* logical signed40/F24 */
    uint32_t frame_id;
    uint32_t start_sample;
    uint32_t input_clips;
    uint32_t bfp_clamped;
} cf_full_output;

cf_full_status cf_full_init(cf_full_context *context, const cf_tables *spectral,
                            const cf_full_tables *tables);
cf_full_status cf_full_reset(cf_full_state *state);
/* Sample-oriented streaming keeps framing independent of host chunk sizes.
 * All objects are caller-owned and non-overlapping; tables stay immutable.
 * Every accepted sample, including incomplete tail, updates previous_pcm.
 * *produced=1 only after a complete frame, at 512,672,832,... samples.
 * preemphasis_q30 may be NULL; otherwise receives every accepted sample.
 * output is updated only on frame processing; it is valid only if produced=1.
 * Numeric failures are sticky until reset; invalid pointer errors do not
 * consume input. FFT wrap remains a reported spectral flag, not a hard error.
 * Clips are limited to UINT32_MAX accepted samples to preserve metadata. */
cf_full_status cf_full_push(const cf_full_context *context, cf_full_state *state,
                            int16_t pcm, cf_full_workspace *workspace,
                            cf_full_output *output, uint32_t *produced,
                            int32_t *preemphasis_q30);
/* Marks the end of the clip without padding/flushing an incomplete frame.
 * Call after supplying all PCM. Idempotent; push then requires reset. */
cf_full_status cf_full_finish(cf_full_state *state);

/* Stage helpers expose exact boundaries for independent integer tests. */
cf_full_status cf_full_preemphasis(int16_t current, int16_t previous, int32_t *output);
cf_full_status cf_full_choose_bfp(const int32_t windowed_q30[CF_N],
                                 int32_t *shift, uint32_t *clamped);
cf_full_status cf_full_quantize(const int32_t windowed_q30[CF_N], int32_t shift,
                                int16_t output[CF_N], uint32_t *clips);
cf_full_status cf_full_floor(uint64_t mel, int32_t exponent, uint32_t *floored);
/* Exact RNE(a *744261118 /2^36) for the ENTIRE signed37 a domain.
 * Output fits int32, but this helper does not impose final logical signed30;
 * cf_full_log checks that bound after the supported log computation. */
cf_full_status cf_full_log_scale(int64_t log2_q30, int32_t *ln_q24);
cf_full_status cf_full_log(uint64_t mel, int32_t exponent,
                           int32_t *log_q24, uint32_t *floored);
cf_full_status cf_full_dct(const cf_full_tables *tables,
                           const int32_t log_q24[CF_MELS],
                           int64_t output[CF_FULL_COEFFICIENTS]);

#endif
