/* Independent transform properties and streaming contracts. No speech data. */
#include "mfcc.h"

#include <fenv.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { CLIP_LENGTH = 832, MAX_FRAMES = 3 };

typedef struct {
    const char *name;
    int passed;
    double maximum_error;
} test_record;

static test_record records[16];
static unsigned record_count;
static float real_work[FFT32_LENGTH];
static float imag_work[FFT32_LENGTH];
static mfcc_state sequential_state;
static mfcc_state interleaved_a;
static mfcc_state interleaved_b;
static mfcc_frame temporary_frame;
static mfcc_frame expected_a[MAX_FRAMES];
static mfcc_frame expected_b[MAX_FRAMES];
static mfcc_frame actual_a[MAX_FRAMES];
static mfcc_frame actual_b[MAX_FRAMES];

static void record(const char *name, int passed, double error)
{
    if (!isfinite(error)) {
        passed = 0;
        error = -1.0; /* Valid JSON sentinel for an invalid arithmetic result. */
    }
    records[record_count].name = name;
    records[record_count].passed = passed;
    records[record_count].maximum_error = error;
    ++record_count;
}

static double larger(double first, double second)
{
    return first > second ? first : second;
}

static void clear_fft(void)
{
    unsigned i;
    for (i = 0; i < FFT32_LENGTH; ++i) {
        real_work[i] = 0.0f;
        imag_work[i] = 0.0f;
    }
}

static void test_fft_properties(void)
{
    const double tau = 6.283185307179586476925286766559;
    const unsigned tone_bin = 19;
    unsigned bin;
    double maximum = 0.0;
    int passed;

    clear_fft();
    real_work[1] = 1.0f;
    passed = fft32_forward(real_work, imag_work) == 0;
    for (bin = 0; bin < FFT32_LENGTH; ++bin) {
        const double angle = -tau * (double)bin / (double)FFT32_LENGTH;
        maximum = larger(maximum, fabs((double)real_work[bin] - cos(angle)));
        maximum = larger(maximum, fabs((double)imag_work[bin] - sin(angle)));
    }
    record("fft_impulse_n1_all_bins_sign_order_scale", passed && maximum <= 2e-5, maximum);

    clear_fft();
    for (bin = 0; bin < FFT32_LENGTH; ++bin) {
        real_work[bin] = 1.0f;
    }
    passed = fft32_forward(real_work, imag_work) == 0;
    maximum = 0.0;
    for (bin = 0; bin < FFT32_LENGTH; ++bin) {
        const double expected = bin == 0 ? 512.0 : 0.0;
        maximum = larger(maximum, fabs((double)real_work[bin] - expected));
        maximum = larger(maximum, fabs((double)imag_work[bin]));
    }
    record("fft_dc_unscaled_512_peak_exact", passed && maximum == 0.0, maximum);

    clear_fft();
    for (bin = 0; bin < FFT32_LENGTH; ++bin) {
        const double angle = tau * (double)((tone_bin * bin) % FFT32_LENGTH)
                             / (double)FFT32_LENGTH;
        real_work[bin] = (float)cos(angle);
        imag_work[bin] = (float)sin(angle);
    }
    passed = fft32_forward(real_work, imag_work) == 0;
    maximum = 0.0;
    for (bin = 0; bin < FFT32_LENGTH; ++bin) {
        const double expected = bin == tone_bin ? 512.0 : 0.0;
        maximum = larger(maximum, fabs((double)real_work[bin] - expected));
        maximum = larger(maximum, fabs((double)imag_work[bin]));
    }
    record("fft_positive_complex_tone_natural_bin19", passed && maximum <= 1e-3, maximum);

    clear_fft();
    passed = fft32_forward(NULL, imag_work) == -1
          && fft32_forward(real_work, NULL) == -1
          && fft32_forward(real_work, real_work) == -1;
    record("fft_reject_null_or_same_work_array", passed, 0.0);

    real_work[5] = INFINITY;
    passed = fft32_forward(real_work, imag_work) == -1;
    clear_fft();
    imag_work[3] = NAN;
    passed = passed && fft32_forward(real_work, imag_work) == -1;
    record("fft_reject_nonfinite_input", passed, 0.0);
}

static int16_t pcm_at(unsigned index, unsigned variant)
{
    const uint32_t value = ((uint32_t)index * UINT32_C(109)
                           + (uint32_t)variant * UINT32_C(1009)
                           + UINT32_C(97)) & UINT32_C(65535);
    return (int16_t)((int32_t)value - INT32_C(32768));
}

/* Compare fields individually: struct padding bytes are not output data. */
static int same_frame(const mfcc_frame *first, const mfcc_frame *second)
{
    return first->start_sample == second->start_sample
        && first->frame_id == second->frame_id
        && memcmp(first->frames, second->frames, sizeof(first->frames)) == 0
        && memcmp(first->windowed, second->windowed, sizeof(first->windowed)) == 0
        && memcmp(first->fft, second->fft, sizeof(first->fft)) == 0
        && memcmp(first->power, second->power, sizeof(first->power)) == 0
        && memcmp(first->mel_energies, second->mel_energies, sizeof(first->mel_energies)) == 0
        && memcmp(first->log_mel, second->log_mel, sizeof(first->log_mel)) == 0
        && memcmp(first->dct, second->dct, sizeof(first->dct)) == 0
        && memcmp(first->mfcc, second->mfcc, sizeof(first->mfcc)) == 0
        && memcmp(&first->frame_energy, &second->frame_energy, sizeof(float)) == 0;
}

static int collect(mfcc_state *state, unsigned length, unsigned variant,
                   mfcc_frame *output, unsigned *count)
{
    unsigned i;
    *count = 0;
    for (i = 0; i < length; ++i) {
        const int result = mfcc_push(state, pcm_at(i, variant), &temporary_frame, NULL);
        if (result == MFCC_ERROR) {
            return 0;
        }
        if (result == MFCC_FRAME_READY) {
            if (*count >= MAX_FRAMES) {
                return 0;
            }
            output[*count] = temporary_frame;
            *count += 1;
        }
    }
    return 1;
}

static void test_streaming_properties(void)
{
    static const unsigned lengths[] = {0, 511, 512, 671, 672, 832};
    static const unsigned expected_counts[] = {0, 0, 1, 1, 2, 3};
    float emphasized[CLIP_LENGTH];
    float previous = 0.0f;
    unsigned test;
    unsigned i;
    unsigned count_a = 0;
    unsigned count_b = 0;
    unsigned replay_a = 0;
    unsigned replay_b = 0;
    int passed = 1;
    double maximum = 0.0;

    for (test = 0; test < sizeof(lengths) / sizeof(lengths[0]); ++test) {
        unsigned produced;
        passed = passed && mfcc_init(&sequential_state) == 0;
        if (!collect(&sequential_state, lengths[test], 0, actual_a, &produced)) {
            passed = 0;
        }
        passed = passed && produced == expected_counts[test]
            && sequential_state.samples_seen == lengths[test]
            && sequential_state.frames_emitted == expected_counts[test];
    }
    record("literal_boundary_counts_discard_incomplete_tail", passed, 0.0);

    passed = mfcc_init(&sequential_state) == 0;
    for (i = 0; i < CLIP_LENGTH; ++i) {
        const float input = (float)pcm_at(i, 0) / 32768.0f;
        const float weighted = 0.95f * previous;
        const float expected = input - weighted;
        float observed = 0.0f;
        const int result = mfcc_push(&sequential_state, pcm_at(i, 0), &temporary_frame, &observed);
        emphasized[i] = expected;
        previous = input;
        passed = passed && result != MFCC_ERROR && observed == expected;
        maximum = larger(maximum, fabs((double)observed - (double)expected));
        if (result == MFCC_FRAME_READY) {
            unsigned index;
            const uint64_t expected_start = (uint64_t)count_a * UINT64_C(160);
            passed = passed && temporary_frame.frame_id == count_a
                && temporary_frame.start_sample == expected_start;
            if (expected_start + MFCC_FRAME_LENGTH > (uint64_t)i + 1) {
                passed = 0;
            } else {
                for (index = 0; index < MFCC_FRAME_LENGTH; ++index) {
                    const float sample = emphasized[(unsigned)expected_start + index];
                    passed = passed && temporary_frame.frames[index] == sample;
                    maximum = larger(maximum, fabs((double)temporary_frame.frames[index] - sample));
                }
            }
            if (count_a < MAX_FRAMES) {
                expected_a[count_a] = temporary_frame;
            } else {
                passed = 0;
            }
            ++count_a;
        }
    }
    record("single_pass_preemphasis_ring_overlap_and_frame_ids", passed && count_a == 3, maximum);

    passed = mfcc_init(&sequential_state) == 0
        && collect(&sequential_state, CLIP_LENGTH, 0, actual_a, &replay_a)
        && replay_a == count_a;
    for (i = 0; i < replay_a && i < MAX_FRAMES; ++i) {
        passed = passed && same_frame(&actual_a[i], &expected_a[i]);
    }
    record("reset_replays_all_stage_values_exactly", passed, 0.0);

    passed = mfcc_init(&sequential_state) == 0
        && collect(&sequential_state, 672, 1, expected_b, &count_b)
        && mfcc_init(&interleaved_a) == 0
        && mfcc_init(&interleaved_b) == 0;
    replay_a = 0;
    replay_b = 0;
    for (i = 0; i < CLIP_LENGTH; ++i) {
        int result = mfcc_push(&interleaved_a, pcm_at(i, 0), &temporary_frame, NULL);
        passed = passed && result != MFCC_ERROR;
        if (result == MFCC_FRAME_READY && replay_a < MAX_FRAMES) {
            actual_a[replay_a++] = temporary_frame;
        } else if (result == MFCC_FRAME_READY) {
            passed = 0;
        }
        if (i < 672) {
            result = mfcc_push(&interleaved_b, pcm_at(i, 1), &temporary_frame, NULL);
            passed = passed && result != MFCC_ERROR;
            if (result == MFCC_FRAME_READY && replay_b < MAX_FRAMES) {
                actual_b[replay_b++] = temporary_frame;
            } else if (result == MFCC_FRAME_READY) {
                passed = 0;
            }
        }
    }
    passed = passed && replay_a == count_a && replay_b == count_b;
    for (i = 0; i < replay_a; ++i) {
        passed = passed && same_frame(&actual_a[i], &expected_a[i]);
    }
    for (i = 0; i < replay_b; ++i) {
        passed = passed && same_frame(&actual_b[i], &expected_b[i]);
    }
    record("two_instances_interleaved_match_sequential_exactly", passed, 0.0);

    passed = mfcc_init(NULL) == MFCC_ERROR
        && mfcc_push(NULL, 0, &temporary_frame, NULL) == MFCC_ERROR
        && mfcc_init(&sequential_state) == 0
        && mfcc_push(&sequential_state, 0, NULL, NULL) == MFCC_ERROR
        && sequential_state.samples_seen == 0;
    sequential_state.initialized_tag = 0;
    passed = passed && mfcc_push(&sequential_state, 0, &temporary_frame, NULL) == MFCC_ERROR;
    record("mfcc_invalid_arguments_do_not_accept_samples", passed, 0.0);

    passed = mfcc_init(&sequential_state) == 0;
    sequential_state.samples_seen = UINT64_MAX;
    passed = passed && mfcc_push(&sequential_state, 0, &temporary_frame, NULL) == MFCC_ERROR
        && sequential_state.samples_seen == UINT64_MAX;
    record("sample_counter_exhaustion_rejected_without_wrap", passed, 0.0);
}

int main(void)
{
    unsigned i;
    unsigned successes = 0;
    const int rounding_ok = fesetround(FE_TONEAREST) == 0 && fegetround() == FE_TONEAREST;
    record("runtime_round_to_nearest", rounding_ok, 0.0);
    if (rounding_ok) {
        test_fft_properties();
        test_streaming_properties();
    }
    for (i = 0; i < record_count; ++i) {
        successes += records[i].passed ? 1u : 0u;
    }
    printf("{\"passed\":%s,\"passed_checks\":%u,\"total_checks\":%u,\"checks\":[",
           successes == record_count ? "true" : "false", successes, record_count);
    for (i = 0; i < record_count; ++i) {
        printf("%s{\"name\":\"%s\",\"passed\":%s,\"max_abs\":%.17g}",
               i == 0 ? "" : ",", records[i].name,
               records[i].passed ? "true" : "false", records[i].maximum_error);
    }
    printf("]}\n");
    return successes == record_count ? 0 : 1;
}
