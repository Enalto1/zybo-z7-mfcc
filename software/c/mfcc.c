#include "mfcc.h"
#include "mfcc_tables.h"

#include <math.h>
#include <stddef.h>

#define MFCC_INITIALIZED_TAG UINT32_C(0x4d464343)

_Static_assert(MFCC_NFFT == FFT32_LENGTH, "MFCC and FFT dimensions must match");
_Static_assert(MFCC_FRAME_LENGTH == MFCC_NFFT, "This profile uses a full-length frame");

int mfcc_init(mfcc_state *state)
{
    unsigned index;
    if (state == NULL) {
        return MFCC_ERROR;
    }
    for (index = 0; index < MFCC_FRAME_LENGTH; ++index) {
        state->ring[index] = 0.0f;
    }
    for (index = 0; index < MFCC_NFFT; ++index) {
        state->fft_re[index] = 0.0f;
        state->fft_im[index] = 0.0f;
    }
    state->previous_input = 0.0f;
    state->samples_seen = UINT64_C(0);
    state->frames_emitted = UINT64_C(0);
    state->write_index = 0;
    state->samples_until_frame = MFCC_FRAME_LENGTH;
    state->initialized_tag = MFCC_INITIALIZED_TAG;
    return MFCC_NO_FRAME;
}

/* Returns zero on success. All sums visit their input indices in ascending order. */
static int calculate_frame(mfcc_state *state, mfcc_frame *out)
{
    unsigned index;
    unsigned filter;
    unsigned cepstrum;
    float frame_energy = 0.0f;

    out->start_sample = state->samples_seen - MFCC_FRAME_LENGTH;
    out->frame_id = state->frames_emitted;
    for (index = 0; index < MFCC_FRAME_LENGTH; ++index) {
        const unsigned ring_index = (state->write_index + index) % MFCC_FRAME_LENGTH;
        const float filtered = state->ring[ring_index];
        const float windowed = filtered * mfcc_window[index];
        out->frames[index] = filtered;
        out->windowed[index] = windowed;
        state->fft_re[index] = windowed;
        state->fft_im[index] = 0.0f;
    }
    if (fft32_forward(state->fft_re, state->fft_im) != 0) {
        return -1;
    }
    for (index = 0; index < MFCC_NBINS; ++index) {
        const float real = state->fft_re[index];
        const float imag = state->fft_im[index];
        const float real_square = real * real;
        const float imag_square = imag * imag;
        const float squared_magnitude = real_square + imag_square;
        const float power = squared_magnitude / 512.0f;
        out->fft[2 * index] = real;
        out->fft[2 * index + 1] = imag;
        out->power[index] = power;
        frame_energy = frame_energy + power;
        if (!isfinite(power) || power < 0.0f) {
            return -1;
        }
    }
    out->frame_energy = frame_energy;
    if (!isfinite(frame_energy)) {
        return -1;
    }

    for (filter = 0; filter < MFCC_NMEL; ++filter) {
        float energy = 0.0f;
        float guarded_energy;
        float log_energy;
        for (index = 0; index < MFCC_NBINS; ++index) {
            const float product = out->power[index] * mfcc_mel[filter][index];
            energy = energy + product;
        }
        if (!isfinite(energy) || energy < 0.0f) {
            return -1;
        }
        guarded_energy = energy < mfcc_log_floor ? mfcc_log_floor : energy;
        log_energy = logf(guarded_energy);
        if (!isfinite(log_energy)) {
            return -1;
        }
        out->mel_energies[filter] = energy;
        out->log_mel[filter] = log_energy;
    }

    for (cepstrum = 0; cepstrum < MFCC_NCEPS; ++cepstrum) {
        float sum = 0.0f;
        float normalized;
        for (filter = 0; filter < MFCC_NMEL; ++filter) {
            const float product = out->log_mel[filter] * mfcc_dct_cosine[cepstrum][filter];
            sum = sum + product;
        }
        normalized = sum * mfcc_dct_scale[cepstrum];
        if (!isfinite(normalized)) {
            return -1;
        }
        out->dct[cepstrum] = normalized;
        out->mfcc[cepstrum] = normalized;
    }
    return 0;
}

int mfcc_push(mfcc_state *state, int16_t sample, mfcc_frame *out,
              float *preemphasis_out)
{
    float input;
    float previous_weighted;
    float filtered;

    if (state == NULL || out == NULL) {
        return MFCC_ERROR;
    }
    if (state->initialized_tag != MFCC_INITIALIZED_TAG ||
        state->write_index >= MFCC_FRAME_LENGTH ||
        state->samples_until_frame == 0 ||
        state->samples_until_frame > MFCC_FRAME_LENGTH ||
        state->samples_seen == UINT64_MAX ||
        state->frames_emitted == UINT64_MAX) {
        return MFCC_ERROR;
    }

    input = (float)sample / 32768.0f;
    previous_weighted = mfcc_preemphasis * state->previous_input;
    filtered = input - previous_weighted;
    if (!isfinite(filtered)) {
        state->initialized_tag = 0;
        return MFCC_ERROR;
    }
    state->previous_input = input;
    state->ring[state->write_index] = filtered;
    state->write_index = (state->write_index + 1) % MFCC_FRAME_LENGTH;
    state->samples_seen += UINT64_C(1);
    state->samples_until_frame -= 1;
    if (preemphasis_out != NULL) {
        *preemphasis_out = filtered;
    }
    if (state->samples_until_frame != 0) {
        return MFCC_NO_FRAME;
    }
    if (calculate_frame(state, out) != 0) {
        state->initialized_tag = 0;
        return MFCC_ERROR;
    }
    state->frames_emitted += UINT64_C(1);
    state->samples_until_frame = MFCC_FRAME_STEP;
    return MFCC_FRAME_READY;
}
