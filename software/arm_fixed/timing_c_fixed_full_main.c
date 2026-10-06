#include "arm_c_fixed_full.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xtime_l.h"

uint64_t arm_c_fixed_full_ticks[ARM_CF_FULL_REPEATS] __attribute__((aligned(64)));

int main(void)
{
    unsigned i;
    int status;
    arm_cf_full_start(2U);
    arm_c_fixed_full_status.warmups = ARM_CF_FULL_WARMUPS;
    arm_c_fixed_full_status.repetitions = ARM_CF_FULL_REPEATS;
    status = arm_cf_full_initialize();
    if ((arm_c_fixed_full_status.timer_control & UINT32_C(0xff01)) != 1U
        || arm_c_fixed_full_status.timer_hz_nominal == 0U
        || arm_c_fixed_full_status.timer_hz_nominal != arm_c_fixed_full_status.cpu_hz_nominal / 2U)
        status = -101;
    for (i = 0; status == 0 && i < ARM_CF_FULL_WARMUPS; ++i) {
        status = arm_cf_full_process(0, UINT32_MAX, 0);
        if (status != 0) break;
        arm_c_fixed_full_status.mismatches = arm_cf_full_compare();
        if (arm_c_fixed_full_status.mismatches != 0U) break;
    }
    if (status == 0 && arm_c_fixed_full_status.mismatches == 0U) {
        for (i = 0; i < ARM_CF_FULL_REPEATS; ++i) {
            XTime first, last;
            XTime_GetTime(&first);
            status = arm_cf_full_process(0, UINT32_MAX, 0);
            XTime_GetTime(&last);
            arm_c_fixed_full_ticks[i] = (uint64_t)(last - first);
            arm_c_fixed_full_status.checksum = arm_cf_full_checksum();
            arm_c_fixed_full_status.mismatches += arm_cf_full_compare();
            if (status != 0 || arm_c_fixed_full_status.mismatches != 0U || last <= first) {
                if (status == 0 && last <= first) status = -100;
                break;
            }
            arm_c_fixed_full_status.completed = i + 1U;
        }
    }
    arm_c_fixed_full_status.error = (uint32_t)status;
    Xil_DCacheFlushRange((INTPTR)arm_c_fixed_full_ticks, (u32)sizeof(arm_c_fixed_full_ticks));
    arm_cf_full_publish();
    for (i = 0; i < arm_c_fixed_full_status.completed; ++i) {
        xil_printf("TICKS %u ", i);
        arm_cf_full_hex64(arm_c_fixed_full_ticks[i]);
        xil_printf("\r\n");
    }
    xil_printf("TIMING completed=%u error=%u frames=%u mismatches=%u checksum=%08x\r\n",
               (unsigned)arm_c_fixed_full_status.completed, (unsigned)arm_c_fixed_full_status.error,
               (unsigned)arm_c_fixed_full_status.frames, (unsigned)arm_c_fixed_full_status.mismatches,
               (unsigned)arm_c_fixed_full_status.checksum);
    arm_cf_full_finished();
    return status != 0 || arm_c_fixed_full_status.mismatches != 0U;
}
