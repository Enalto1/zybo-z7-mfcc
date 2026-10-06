#ifndef ARM_MFCC_PROTOCOL_H
#define ARM_MFCC_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>
#include "mfcc.h"

/* All host-visible integers/IEEE754 floats are little-endian on this target. */
#define ARM_CONTROL_MAGIC UINT32_C(0x4d464343)
#define ARM_STATUS_MAGIC UINT32_C(0x41524d53)
#define ARM_LAYOUT_MAGIC UINT32_C(0x41524d4c)
#define ARM_PROTOCOL_VERSION UINT32_C(1)
#define ARM_PCM_CAPACITY UINT32_C(262144)
#define ARM_OUTPUT_CAPACITY UINT32_C(2048)
#define ARM_TIMING_CAPACITY UINT32_C(100)
#define ARM_MAX_WARMUPS UINT32_C(20)
#define ARM_DDR_TEST_WORDS UINT32_C(1024)

enum arm_command { ARM_COMMAND_RUN = 1 };
enum arm_mode { ARM_MODE_VALIDATION = 1, ARM_MODE_TRACE = 2, ARM_MODE_TIMING = 3 };
enum arm_state { ARM_STATE_BOOT = 0, ARM_STATE_READY = 1, ARM_STATE_RUNNING = 2,
                 ARM_STATE_DONE = 3, ARM_STATE_ERROR = 4 };
enum arm_error {
    ARM_ERROR_NONE = 0, ARM_ERROR_DDR = 1, ARM_ERROR_CONTROL_MAGIC = 2,
    ARM_ERROR_CONTROL_VERSION = 3, ARM_ERROR_COMMAND = 4, ARM_ERROR_MODE = 5,
    ARM_ERROR_SAMPLE_COUNT = 6, ARM_ERROR_OUTPUT_CAPACITY = 7,
    ARM_ERROR_INPUT_CRC = 8, ARM_ERROR_TRACE_FRAME = 9,
    ARM_ERROR_TIMING_PARAMETERS = 10, ARM_ERROR_VALIDATION_REQUIRED = 11,
    ARM_ERROR_CORE = 12, ARM_ERROR_FRAME_COUNT = 13, ARM_ERROR_FPSCR = 14,
    ARM_ERROR_RESERVED_CONTROL = 15
};

/* Exact 16-word control ABI. Write command last while CPU is stopped at ready. */
typedef struct {
    uint32_t magic, version, command, mode;
    uint32_t sample_count, expected_crc32, trace_frame, warmups;
    uint32_t repeats, validation_passed;
    uint32_t reserved[6];
} arm_control_block;

/* Exact 32-word status ABI; words are numbered by this field order. */
typedef struct {
    uint32_t magic, version, state, error;
    uint32_t mode, sample_count, frame_count, input_crc32;
    uint32_t expected_crc32, ddr_test_passed, ddr_failure_index, ddr_expected;
    uint32_t ddr_actual, fpscr_before, fpscr_active, fpscr_after;
    uint32_t sctlr, cpu_hz, timer_hz, warmups_done;
    uint32_t repeats_done, trace_valid, output_checksum, trace_frame;
    uint32_t timer_overhead_low, timer_overhead_high, control_bytes, status_bytes;
    uint32_t cpsr, global_timer_control, l2_cache_control, reserved;
} arm_status_block;

typedef struct {
    uint32_t frame_id;
    uint32_t start_sample;
    float coeff[MFCC_NCEPS];
} arm_output_record;

typedef struct {
    uint32_t ticks_low, ticks_high, frame_count, checksum;
} arm_timing_record;

/* Exact 32-word descriptor: do not infer the internal mfcc_frame ABI on host. */
typedef struct {
    uint32_t magic, version, control_bytes, status_bytes;
    uint32_t pcm_capacity, output_capacity, result_bytes, trace_bytes;
    uint32_t trace_start_sample_offset, trace_frame_id_offset;
    uint32_t trace_frames_offset, trace_windowed_offset, trace_fft_offset;
    uint32_t trace_power_offset, trace_mel_offset, trace_log_offset;
    uint32_t trace_dct_offset, trace_mfcc_offset, trace_frame_energy_offset;
    uint32_t frame_length, fft_bins, mel_count, coefficient_count;
    uint32_t trace_scalar_bytes, trace_id_bytes, timing_capacity, timing_record_bytes;
    uint32_t reserved[5];
} arm_layout_descriptor;

_Static_assert(sizeof(arm_control_block) == 64, "Control ABI must be 64 bytes");
_Static_assert(sizeof(arm_status_block) == 128, "Status ABI must be 128 bytes");
_Static_assert(sizeof(arm_output_record) == 60, "Output record ABI must be 60 bytes");
_Static_assert(offsetof(arm_output_record, coeff) == 8, "Coefficient record offset");
_Static_assert(sizeof(arm_timing_record) == 16, "Timing record ABI must be 16 bytes");
_Static_assert(sizeof(arm_layout_descriptor) == 128, "Layout ABI must be 128 bytes");

extern volatile arm_control_block arm_control;
extern volatile arm_status_block arm_status;
extern const arm_layout_descriptor arm_layout;
extern volatile uint32_t arm_ddr_test[ARM_DDR_TEST_WORDS];
/* ARM PLL, DDR PLL, IO PLL, ARM clock control; configuration, not measurement. */
extern volatile uint32_t arm_clock_registers[4];

/* MFCC executable exports these; hello executable only exports common symbols. */
extern int16_t arm_pcm[ARM_PCM_CAPACITY];
extern arm_output_record arm_results[ARM_OUTPUT_CAPACITY];
extern float arm_preemphasis[ARM_PCM_CAPACITY];
extern mfcc_frame arm_trace;
extern arm_timing_record arm_timing_records[ARM_TIMING_CAPACITY];

void arm_ready_breakpoint(void);
void arm_result_breakpoint(void);

#endif
