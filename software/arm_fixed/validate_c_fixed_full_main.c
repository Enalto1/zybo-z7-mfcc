#include "arm_c_fixed_full.h"
#include "xil_cache.h"
#include "xil_printf.h"

int32_t arm_c_fixed_full_preemphasis[C_FIXED_FULL_DEV_SAMPLES] __attribute__((aligned(64)));
cf_full_output arm_c_fixed_full_trace __attribute__((aligned(64)));
/* Set while halted at arm_cf_full_ready for a future board validation run.
 * UINT32_MAX disables selected-frame trace; this does not alter computation. */
volatile uint32_t arm_c_fixed_full_trace_frame __attribute__((aligned(64))) = 0U;
volatile uint32_t arm_c_fixed_full_dump_all_pre __attribute__((aligned(64))) = 0U;

int main(void)
{
    int status;
    uint32_t selected, dump_pre, sample;
    arm_cf_full_start(1U);
    status = arm_cf_full_initialize();
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_full_trace_frame, (u32)sizeof(arm_c_fixed_full_trace_frame));
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_full_dump_all_pre, (u32)sizeof(arm_c_fixed_full_dump_all_pre));
    arm_cf_full_ready();
    Xil_DCacheInvalidateRange((INTPTR)&arm_c_fixed_full_trace_frame, (u32)sizeof(arm_c_fixed_full_trace_frame));
    Xil_DCacheInvalidateRange((INTPTR)&arm_c_fixed_full_dump_all_pre, (u32)sizeof(arm_c_fixed_full_dump_all_pre));
    selected = arm_c_fixed_full_trace_frame;
    dump_pre = arm_c_fixed_full_dump_all_pre;
    if ((selected >= C_FIXED_FULL_DEV_FRAMES && selected != UINT32_MAX) || dump_pre > 1U)
        status = -204;
    if (status == 0)
        status = arm_cf_full_process(arm_c_fixed_full_preemphasis, selected,
                                     selected == UINT32_MAX ? 0 : &arm_c_fixed_full_trace);
    arm_c_fixed_full_status.error = (uint32_t)status;
    if (status == 0) {
        arm_c_fixed_full_status.mismatches = arm_cf_full_compare();
        arm_c_fixed_full_status.checksum = arm_cf_full_checksum();
        arm_cf_full_dump_results();
        if (selected != UINT32_MAX)
            arm_cf_full_dump_trace(&arm_c_fixed_full_trace, arm_c_fixed_full_preemphasis);
        if (dump_pre != 0U) {
            for (sample = 0; sample < C_FIXED_FULL_DEV_SAMPLES; ++sample)
                xil_printf("PRE_ALL %u %d\r\n", (unsigned)sample, (int)arm_c_fixed_full_preemphasis[sample]);
        }
    }
    arm_c_fixed_full_status.completed = 1U;
    Xil_DCacheFlushRange((INTPTR)arm_c_fixed_full_preemphasis, (u32)sizeof(arm_c_fixed_full_preemphasis));
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_full_trace, (u32)sizeof(arm_c_fixed_full_trace));
    arm_cf_full_publish();
    xil_printf("VALIDATION completed=%u error=%u frames=%u mismatches=%u checksum=%08x\r\n",
               (unsigned)arm_c_fixed_full_status.completed, (unsigned)arm_c_fixed_full_status.error,
               (unsigned)arm_c_fixed_full_status.frames, (unsigned)arm_c_fixed_full_status.mismatches,
               (unsigned)arm_c_fixed_full_status.checksum);
    arm_cf_full_finished();
    return status != 0 || arm_c_fixed_full_status.mismatches != 0U;
}
