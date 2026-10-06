#ifndef MFCC_REFERENCE_H
#define MFCC_REFERENCE_H

#include <stdint.h>
#include "fft32.h"

enum {
    MFCC_SAMPLE_RATE = 16000,
    MFCC_FRAME_LENGTH = 512,
    MFCC_FRAME_STEP = 160,
    MFCC_NFFT = 512,
    MFCC_NBINS = 257,
    MFCC_NMEL = 26,
    MFCC_NCEPS = 13,
    MFCC_FFT_FLOATS = 514
};

enum {
    MFCC_ERROR = -1,
    MFCC_NO_FRAME = 0,
    MFCC_FRAME_READY = 1
};

/*
 * Public workspace for allocation by the caller. Use mfcc_init before pushing
 * samples; fields must not be changed during a clip. Each instance is independent
 * and must not be used concurrently. No global mutable processing state exists.
 */
typedef struct {
    float ring[MFCC_FRAME_LENGTH];
    float fft_re[MFCC_NFFT];
    float fft_im[MFCC_NFFT];
    float previous_input;
    uint64_t samples_seen;
    uint64_t frames_emitted;
    uint32_t write_index;
    uint32_t samples_until_frame;
    uint32_t initialized_tag;
} mfcc_state;

/*
 * Stage-visible raw13 output, all numeric stages in float32. Frames are ordered
 * oldest to newest. fft stores re[0],im[0],...,re[256],im[256]; negative-frequency
 * bins are omitted and the power bins are NOT doubled. frame_energy is the sum
 * of these 257 power bins, retained for diagnostics and not substituted into C0.
 *
 * Do not serialize this struct directly: alignment/padding is implementation
 * defined. A host adapter writes individual arrays with an explicit byte order.
 */
typedef struct {
    uint64_t start_sample;
    uint64_t frame_id;
    float frames[MFCC_FRAME_LENGTH];
    float windowed[MFCC_FRAME_LENGTH];
    float fft[MFCC_FFT_FLOATS];
    float power[MFCC_NBINS];
    float mel_energies[MFCC_NMEL];
    float log_mel[MFCC_NMEL];
    float dct[MFCC_NCEPS];
    float mfcc[MFCC_NCEPS];
    float frame_energy;
} mfcc_frame;

/* Reset ALL clip state. Return 0 on success, -1 if state is NULL. */
int mfcc_init(mfcc_state *state);

/*
 * Accept exactly one mono 16 kHz PCM16 sample. x = sample/32768.0f, then compute
 * continuous y[n] = x[n] - alpha*x[n-1] once before framing. The first previous
 * input is zero. Emit one frame after 512 samples and every 160 samples after it;
 * incomplete final frames are discarded by stopping input, with no tail flush.
 * There is no lifter, energy replacement, delta, CMVN, I/O, timing or allocation.
 *
 * out is required on every call. preemphasis_out may be NULL; otherwise it gets
 * the filtered value for every accepted sample. The three pointer arguments must
 * not overlap. out is unchanged on return 0 and is a complete frame on return 1.
 *
 * Return -1 for invalid pointers/state or uint64 counter exhaustion, before input
 * consumption. An unexpected numerical failure also returns -1, invalidates the
 * state, and may leave partial output after consuming that sample; reinitialize
 * before reuse. Runtime rounding mode must remain round-to-nearest-ties-to-even.
 */
int mfcc_push(mfcc_state *state, int16_t sample, mfcc_frame *out,
              float *preemphasis_out);

#endif
