#ifndef MFCC_FP32_ACCEL_H
#define MFCC_FP32_ACCEL_H

#include <stddef.h>
#include <stdint.h>

/* FP32 PS/PL transport ABI 0x00010001; independent of fixed accelerator. */
#define MFCC_FP32_ACCEL_BASE UINT32_C(0x43c00000)
#define MFCC_FP32_ACCEL_MAX_SAMPLES UINT32_C(262144)
#define MFCC_FP32_ACCEL_FRAME_LENGTH UINT32_C(512)
#define MFCC_FP32_ACCEL_HOP UINT32_C(160)
#define MFCC_FP32_ACCEL_COEFFICIENTS UINT32_C(13)
#define MFCC_FP32_ACCEL_CONTRACT_TAG UINT32_C(0xc556a8e8)

enum mfcc_fp32_accel_register {
    MFCC_R_ID=0x00, MFCC_R_ABI=0x04, MFCC_R_CONTROL=0x08,
    MFCC_R_STATUS=0x0c, MFCC_R_SAMPLE_COUNT=0x10, MFCC_R_PCM=0x14,
    MFCC_R_OUT_LO=0x18, MFCC_R_OUT_HI=0x1c, MFCC_R_OUT_FRAME=0x20,
    MFCC_R_OUT_META=0x24, MFCC_R_POP=0x28, MFCC_R_ERRORS=0x2c,
    MFCC_R_WRITTEN=0x30, MFCC_R_CONSUMED=0x34,
    MFCC_R_CAPTURED=0x38, MFCC_R_POPPED=0x3c,
    MFCC_R_CORE_ID=0x40, MFCC_R_FORMAT=0x44,
    MFCC_R_FRAME_LENGTH=0x48, MFCC_R_HOP=0x4c, MFCC_R_NCOEF=0x50,
    MFCC_R_MAX_SAMPLES=0x54, MFCC_R_CYCLES_LO=0x58,
    MFCC_R_CYCLES_HI=0x5c, MFCC_R_CONTRACT_TAG=0x60,
    MFCC_R_CORE_ERROR_DETAIL=0x64
};
enum mfcc_fp32_accel_command { MFCC_CMD_START=1, MFCC_CMD_ABORT=2, MFCC_CMD_CLEAR=4 };
enum mfcc_fp32_accel_status_bits {
    MFCC_S_BUSY=1, MFCC_S_DONE=2, MFCC_S_ERROR=4,
    MFCC_S_PCM_READY=8, MFCC_S_OUTPUT_VALID=16
};
enum mfcc_fp32_accel_result {
    MFCC_FP32_ACCEL_OK=0, MFCC_FP32_ACCEL_ARGUMENT=-1, MFCC_FP32_ACCEL_IDENTITY=-2,
    MFCC_FP32_ACCEL_IO=-3, MFCC_FP32_ACCEL_HARDWARE=-4,
    MFCC_FP32_ACCEL_SEQUENCE=-5, MFCC_FP32_ACCEL_TIMEOUT=-6
};

/* Callbacks use byte offsets; reads never pop and writes are full 32-bit words.
 * A nonzero callback result means an IO error. One software owner is required.
 * ticks must be a monotonic modulo-2^64 counter. The finite poll budget also
 * terminates a responsive MMIO loop if that timer has stopped.
 */
typedef struct {
    void *context;
    int (*read32)(void *context, uint32_t offset, uint32_t *value);
    int (*write32)(void *context, uint32_t offset, uint32_t value);
    uint64_t (*ticks)(void *context);
} mfcc_fp32_accel_bus;

typedef struct {
    uint64_t timeout_ticks; /* Nonzero and at most INT64_MAX. */
    uint32_t max_polls;     /* Nonzero absolute loop bound. */
} mfcc_fp32_accel_limits;

/* Explicit 24-byte DDR record. IEEE754 binary32 bits, zero-extended to 64.
 * No numeric cast or arithmetic is applied; BFP must be zero for this core. */
typedef struct {
    uint64_t value_bits;
    uint32_t frame;
    uint32_t index;
    int32_t bfp_s;
    uint32_t flags; /* bit0 last, bit1 error */
} mfcc_fp32_accel_record;

typedef struct {
    uint64_t elapsed_ticks;
    uint64_t hardware_busy_cycles;
    uint32_t polls, samples_sent, records_received, expected_frames;
    uint32_t status, error_flags;
    uint32_t input_written, input_consumed, output_captured, output_popped;
    uint32_t last_lo, last_hi, last_frame, last_meta;
    uint32_t failed_offset, abort_attempted, abort_failed;
    uint32_t core_error_detail; /* RO 0x64, FIRST native 12-bit fault vector/code; prior ABI padding. */
} mfcc_fp32_accel_stats;

_Static_assert(sizeof(mfcc_fp32_accel_record)==24, "DDR output record ABI");
_Static_assert(offsetof(mfcc_fp32_accel_record, frame)==8, "DDR output frame offset");

uint32_t mfcc_fp32_accel_frames(uint32_t samples);
int mfcc_fp32_accel_probe(const mfcc_fp32_accel_bus *bus, uint32_t *failed_offset);
int mfcc_fp32_accel_decode_bits(uint32_t low, uint32_t high, uint64_t *value);
int mfcc_fp32_accel_run(const mfcc_fp32_accel_bus *bus, const int16_t *pcm,
                   uint32_t samples, mfcc_fp32_accel_record *records,
                   size_t record_capacity, const mfcc_fp32_accel_limits *limits,
                   mfcc_fp32_accel_stats *stats);

#endif
