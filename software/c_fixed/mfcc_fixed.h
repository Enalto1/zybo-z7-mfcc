#ifndef C_FIXED_MFCC_FIXED_H
#define C_FIXED_MFCC_FIXED_H

#include <stdint.h>

/* Partial FFT->Power->Mel numerical profile, matching published contract v1.
 * This is not a PCM->raw13 MFCC API: front end, BFP choice, log and DCT are
 * absent. The generated c_fixed_contract.h pins the exact publication version
 * and hash per executable. Profile support is not numerical acceptance. */
#define CF_PROFILE_ID "fft512.in16f15.data20f19.tw16f15.power40.mel60fw16"
enum { CF_N = 512, CF_BINS = 257, CF_MELS = 26, CF_ROM = 1024,
       CF_FFT_GROUPS = 5, CF_INPUT_W = 16, CF_INPUT_F = 15,
       CF_DATA_W = 20, CF_DATA_F = 19, CF_TWIDDLE_W = 16,
       CF_TWIDDLE_F = 15, CF_POWER_W = 40, CF_MEL_W = 60,
       CF_MEL_F = 16, CF_PHYSICAL_SHIFT = 9 };

typedef enum { CF_OK = 0, CF_BAD_ARGUMENT = 1, CF_BAD_TABLE = 2,
               CF_BAD_EXPONENT = 3, CF_POWER_RANGE = 4, CF_MEL_RANGE = 5 } cf_status;

typedef struct {
    int16_t twiddle_re[CF_ROM];
    int16_t twiddle_im[CF_ROM];
    uint32_t mel[CF_MELS][CF_BINS];
} cf_tables;

/* Keep tables immutable throughout the context lifetime. The host owns
 * provenance/hash verification; cf_init verifies supported numeric bounds. */
typedef struct { const cf_tables *tables; uint32_t ready; } cf_context;
typedef struct { int32_t current[2][CF_N]; int32_t scratch[2][CF_N]; } cf_workspace;

typedef struct {
    int32_t fft_re[CF_N];
    int32_t fft_im[CF_N];
    uint64_t power[CF_BINS];
    uint64_t mel[CF_MELS];
    int32_t bfp_shift;
    int32_t power_exp2;
    int32_t mel_exp2;
    uint32_t fft_overflow;
    uint32_t power_overflow;
    uint32_t mel_overflow;
} cf_result;

/* Diagnostic only. groups[0..3] are complete R2^2 group boundaries at
 * block lengths 512,128,32,8; groups[4] is the trailing radix-2 boundary.
 * Arrays are internal ordinal order (final group is bit-reversed order).
 * axis[0] is real, axis[1] imaginary. NULL avoids all trace copies. */
typedef struct {
    int32_t promoted[2][CF_N];
    int32_t groups[CF_FFT_GROUPS][2][CF_N];
    uint32_t overflow_after_group[CF_FFT_GROUPS];
} cf_fft_trace;

cf_status cf_validate_tables(const cf_tables *tables);
cf_status cf_init(cf_context *context, const cf_tables *tables);
/* Caller provides distinct, non-overlapping input, context/tables, workspace,
 * output and optional trace objects. Inputs may include -32768 for adversarial
 * FFT tests; upstream target0975 quantization normally uses symmetric clamp.
 * s in [-2,24] is supplied by the pinned host fixture, never selected here.
 * Wrap overflow is sticky and observable but does not suppress FFT outputs.
 * CF_OK with fft_overflow!=0 is an exact port result, not a no-overflow pass. */
cf_status cf_process(const cf_context *context,
                     const int16_t input_re[CF_N], const int16_t input_im[CF_N],
                     int32_t bfp_shift, cf_workspace *workspace,
                     cf_result *output, cf_fft_trace *trace);

#endif
