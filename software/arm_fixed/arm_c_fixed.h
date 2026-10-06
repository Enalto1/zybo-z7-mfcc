#ifndef ARM_C_FIXED_H
#define ARM_C_FIXED_H

#include <stdint.h>
#include "mfcc_fixed.h"

#define ARM_CF_REPETITIONS 100U
#define ARM_CF_WARMUPS 3U
#define ARM_CF_MAGIC UINT32_C(0x43464658)

/* Debugger-readable metadata, not a persisted struct ABI. UART fields are
 * serialized explicitly. No PCM/front-end/log/DCT is claimed by this adapter. */
typedef struct {
    uint32_t magic;
    uint32_t mode; /* 1: accuracy dump, 2: repeated-frame computation timing */
    uint32_t completed;
    uint32_t core_error;
    uint32_t mismatches;
    uint32_t checksum;
    uint32_t cpu_hz_nominal;
    uint32_t timer_hz_nominal;
    uint32_t sctlr;
    uint32_t cpsr;
    uint32_t l2_cache_control;
    uint32_t global_timer_control;
    uint32_t arm_clock_registers[4];
    uint32_t workspace_bytes;
    uint32_t result_bytes;
    uint32_t trace_bytes;
    uint32_t tables_bytes;
    uint32_t warmups;
    uint32_t repetitions;
    uint64_t timer_read_pair_ticks;
} arm_cf_status;

extern volatile arm_cf_status arm_c_fixed_status;
extern cf_workspace arm_c_fixed_workspace;
extern cf_result arm_c_fixed_result;

void arm_cf_start(uint32_t mode);
uint32_t arm_cf_compare(const cf_result *actual, const cf_result *expected);
uint32_t arm_cf_checksum(const cf_result *result);
void arm_cf_publish(void);
void arm_cf_dump(const cf_result *result);
void arm_cf_dump_trace(const cf_fft_trace *trace);
void arm_cf_hex64(uint64_t value);
void arm_cf_finished(void);

#endif
