#include "arm_support.h"

#include <string.h>
#include "xtime_l.h"

#define ALIGNED64 __attribute__((aligned(64)))

int16_t arm_pcm[ARM_PCM_CAPACITY] ALIGNED64;
arm_output_record arm_results[ARM_OUTPUT_CAPACITY] ALIGNED64;
float arm_preemphasis[ARM_PCM_CAPACITY] ALIGNED64;
mfcc_frame arm_trace ALIGNED64;
arm_timing_record arm_timing_records[ARM_TIMING_CAPACITY] ALIGNED64;

/* Core state and full stage workspace are linker-owned DDR, never large stack objects. */
static mfcc_state processing_state ALIGNED64;
static mfcc_frame processing_frame ALIGNED64;
static volatile uint32_t checksum_sink;

static uint32_t expected_frames(uint32_t samples)
{
    return samples < MFCC_FRAME_LENGTH ? 0U
        : 1U + (samples - MFCC_FRAME_LENGTH) / MFCC_FRAME_STEP;
}

static uint32_t consume_frame(uint32_t checksum, const mfcc_frame *frame)
{
    uint32_t coefficient;
    checksum ^= (uint32_t)frame->frame_id;
    checksum ^= (uint32_t)frame->start_sample;
    for (coefficient = 0; coefficient < MFCC_NCEPS; ++coefficient) {
        uint32_t bits;
        memcpy(&bits, &frame->mfcc[coefficient], sizeof(bits));
        checksum ^= bits;
        checksum = (checksum << 5) | (checksum >> 27);
        checksum += UINT32_C(0x9e3779b9);
    }
    return checksum;
}

/* No UART, transfer, CRC, cache maintenance, timer call, or debugger stop here.
 * Timing includes init, every mfcc_push, frame checks, and output consumption.
 * Validation/trace additionally copies outputs; those copies are not timed.
 */
static uint32_t process_clip(uint32_t samples, uint32_t mode, uint32_t trace_frame,
                             uint32_t *frame_count, uint32_t *checksum)
{
    uint32_t sample_index;
    uint32_t count = 0;
    uint32_t sum = UINT32_C(0x6d666363);
    if (mfcc_init(&processing_state) != 0) {
        return ARM_ERROR_CORE;
    }
    for (sample_index = 0; sample_index < samples; ++sample_index) {
        float *pre = mode == ARM_MODE_TRACE ? &arm_preemphasis[sample_index] : NULL;
        int emitted = mfcc_push(&processing_state, arm_pcm[sample_index], &processing_frame, pre);
        if (emitted == MFCC_ERROR) {
            return ARM_ERROR_CORE;
        }
        if (emitted == MFCC_FRAME_READY) {
            if (count >= ARM_OUTPUT_CAPACITY
                || processing_frame.frame_id != count
                || processing_frame.start_sample != (uint64_t)count * MFCC_FRAME_STEP) {
                return ARM_ERROR_FRAME_COUNT;
            }
            sum = consume_frame(sum, &processing_frame);
            if (mode != ARM_MODE_TIMING) {
                uint32_t coefficient;
                arm_results[count].frame_id = count;
                arm_results[count].start_sample = (uint32_t)processing_frame.start_sample;
                for (coefficient = 0; coefficient < MFCC_NCEPS; ++coefficient) {
                    arm_results[count].coeff[coefficient] = processing_frame.mfcc[coefficient];
                }
            }
            if (mode == ARM_MODE_TRACE && count == trace_frame) {
                arm_trace = processing_frame;
            }
            ++count;
        }
    }
    if (count != expected_frames(samples)) {
        return ARM_ERROR_FRAME_COUNT;
    }
    *frame_count = count;
    *checksum = sum;
    checksum_sink = sum;
    return ARM_ERROR_NONE;
}

static uint32_t validate_control(const arm_control_block *control)
{
    uint32_t index;
    if (control->magic != ARM_CONTROL_MAGIC) {
        return ARM_ERROR_CONTROL_MAGIC;
    }
    if (control->version != ARM_PROTOCOL_VERSION) {
        return ARM_ERROR_CONTROL_VERSION;
    }
    if (control->command != ARM_COMMAND_RUN) {
        return ARM_ERROR_COMMAND;
    }
    if (control->mode < ARM_MODE_VALIDATION || control->mode > ARM_MODE_TIMING) {
        return ARM_ERROR_MODE;
    }
    if (control->sample_count > ARM_PCM_CAPACITY) {
        return ARM_ERROR_SAMPLE_COUNT;
    }
    if (expected_frames(control->sample_count) > ARM_OUTPUT_CAPACITY) {
        return ARM_ERROR_OUTPUT_CAPACITY;
    }
    if (control->mode == ARM_MODE_TRACE) {
        uint32_t frames = expected_frames(control->sample_count);
        if ((frames == 0U && control->trace_frame != UINT32_MAX)
            || (frames != 0U && control->trace_frame >= frames)) {
            return ARM_ERROR_TRACE_FRAME;
        }
    }
    if (control->mode == ARM_MODE_TIMING) {
        if (control->warmups > ARM_MAX_WARMUPS || control->repeats == 0U
            || control->repeats > ARM_TIMING_CAPACITY
            || expected_frames(control->sample_count) == 0U) {
            return ARM_ERROR_TIMING_PARAMETERS;
        }
        if (control->validation_passed != 1U) {
            return ARM_ERROR_VALIDATION_REQUIRED;
        }
    } else if (control->warmups != 0U || control->repeats != 0U
               || control->validation_passed != 0U) {
        return ARM_ERROR_TIMING_PARAMETERS;
    }
    for (index = 0; index < 6U; ++index) {
        if (control->reserved[index] != 0U) {
            return ARM_ERROR_RESERVED_CONTROL;
        }
    }
    return ARM_ERROR_NONE;
}

static uint32_t run_job(const arm_control_block *control)
{
    uint32_t count = 0;
    uint32_t checksum = 0;
    uint32_t error = validate_control(control);
    if (error != ARM_ERROR_NONE) {
        return error;
    }
    arm_status.mode = control->mode;
    arm_status.sample_count = control->sample_count;
    arm_status.expected_crc32 = control->expected_crc32;
    arm_status.trace_frame = control->trace_frame;
    /* Host downloads while halted; invalidation discards CPU's pre-download copy. */
    arm_invalidate(arm_pcm, (size_t)control->sample_count * sizeof(arm_pcm[0]));
    arm_status.input_crc32 = arm_crc32(arm_pcm, (size_t)control->sample_count * sizeof(arm_pcm[0]));
    if (arm_status.input_crc32 != control->expected_crc32) {
        return ARM_ERROR_INPUT_CRC;
    }
    arm_status.state = ARM_STATE_RUNNING;
    if (control->mode != ARM_MODE_TIMING) {
        error = process_clip(control->sample_count, control->mode, control->trace_frame,
                             &count, &checksum);
        if (error != ARM_ERROR_NONE) {
            return error;
        }
        arm_status.frame_count = count;
        arm_status.output_checksum = checksum;
        arm_flush(arm_results, (size_t)count * sizeof(arm_results[0]));
        if (control->mode == ARM_MODE_TRACE) {
            if (count != 0U) {
                arm_status.trace_valid = 1;
                arm_flush(&arm_trace, sizeof(arm_trace));
            }
            arm_flush(arm_preemphasis, (size_t)control->sample_count * sizeof(arm_preemphasis[0]));
        }
    } else {
        uint32_t repetition;
        /* The one input CRC and all warmups precede the timed repetitions. */
        for (repetition = 0; repetition < control->warmups; ++repetition) {
            error = process_clip(control->sample_count, ARM_MODE_TIMING, 0U, &count, &checksum);
            if (error != ARM_ERROR_NONE) {
                return error;
            }
            ++arm_status.warmups_done;
        }
        for (repetition = 0; repetition < control->repeats; ++repetition) {
            XTime before, after;
            uint64_t elapsed;
            XTime_GetTime(&before);
            error = process_clip(control->sample_count, ARM_MODE_TIMING, 0U, &count, &checksum);
            XTime_GetTime(&after);
            if (error != ARM_ERROR_NONE) {
                return error;
            }
            elapsed = after - before;
            arm_timing_records[repetition].ticks_low = (uint32_t)elapsed;
            arm_timing_records[repetition].ticks_high = (uint32_t)(elapsed >> 32);
            arm_timing_records[repetition].frame_count = count;
            arm_timing_records[repetition].checksum = checksum;
            arm_status.output_checksum = checksum;
            ++arm_status.repeats_done;
        }
        arm_status.frame_count = count;
        arm_flush(arm_timing_records, (size_t)control->repeats * sizeof(arm_timing_records[0]));
    }
    return ARM_ERROR_NONE;
}

int main(void)
{
    arm_control_block control;
    uint32_t error;
    if (arm_startup("ARM_MFCC_READY_V1") != 0) {
        arm_result_breakpoint();
        arm_idle();
    }
    /* Startup BSS clearing can leave dirty zeros. Clean the entire host-owned
     * buffer before JTAG writes, including any future partial final cache line.
     * No CPU read/write of arm_pcm occurs between this and the ready breakpoint.
     */
    arm_flush(arm_pcm, sizeof(arm_pcm));
    arm_invalidate(arm_pcm, sizeof(arm_pcm));
    arm_ready_breakpoint();
    /* One-shot protocol: there is no cached spin polling and no implicit rerun. */
    arm_invalidate((const void *)&arm_control, sizeof(arm_control));
    control = arm_control;
    error = run_job(&control);
    arm_complete(error);
    arm_result_breakpoint();
    arm_idle();
}
