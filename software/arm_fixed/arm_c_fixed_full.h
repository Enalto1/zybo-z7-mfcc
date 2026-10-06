#ifndef ARM_C_FIXED_FULL_H
#define ARM_C_FIXED_FULL_H

#include <stdint.h>
#include "mfcc_fixed_full.h"
#include "c_fixed_full_vectors.h"

#define ARM_CF_FULL_MAGIC UINT32_C(0x43464632)
#define ARM_CF_FULL_WARMUPS 3U
#define ARM_CF_FULL_REPEATS 30U

_Static_assert(C_FIXED_FULL_DEV_SAMPLES == 85920, "Pinned development clip sample count");
_Static_assert(C_FIXED_FULL_DEV_FRAMES == 534, "Pinned complete frame count");

typedef struct {
    uint32_t magic, mode, completed, error, mismatches, checksum;
    uint32_t frames, samples, cpu_hz_nominal, timer_hz_nominal;
    uint32_t sctlr, cpsr, l2_cache_control, timer_control;
    uint32_t arm_clock_registers[4];
    uint32_t state_bytes, workspace_bytes, frame_bytes, context_bytes;
    uint32_t spectral_tables_bytes, full_tables_bytes, warmups, repetitions;
    uint64_t timer_read_pair_ticks;
} arm_cf_full_status;

typedef struct {
    int64_t raw13[13];
    int32_t metadata[8]; /* frame_id,start,s,power_exp,mel_exp,fftov,clips,clamp */
} arm_cf_full_record;

extern volatile arm_cf_full_status arm_c_fixed_full_status;
extern arm_cf_full_record arm_c_fixed_full_results[C_FIXED_FULL_DEV_FRAMES];
extern cf_full_state arm_c_fixed_full_state;
extern cf_full_workspace arm_c_fixed_full_workspace;
extern cf_full_output arm_c_fixed_full_frame;

void arm_cf_full_start(uint32_t mode);
int arm_cf_full_initialize(void);
int arm_cf_full_process(int32_t *preemphasis, uint32_t trace_frame, cf_full_output *trace);
uint32_t arm_cf_full_compare(void);
uint32_t arm_cf_full_checksum(void);
void arm_cf_full_dump_results(void);
void arm_cf_full_dump_trace(const cf_full_output *frame, const int32_t *preemphasis);
void arm_cf_full_publish(void);
void arm_cf_full_hex64(uint64_t value);
void arm_cf_full_ready(void);
void arm_cf_full_finished(void);

#endif
