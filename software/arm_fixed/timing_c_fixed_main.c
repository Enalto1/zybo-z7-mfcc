#include "arm_c_fixed.h"
#include "c_fixed_tables.h"
#include "c_fixed_vectors.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xtime_l.h"

uint64_t arm_c_fixed_ticks[ARM_CF_REPETITIONS] __attribute__((aligned(64)));
static cf_context context;

int main(void)
{
    unsigned i;
    int status = 0;
    arm_cf_start(2U);
    arm_c_fixed_status.warmups = ARM_CF_WARMUPS;
    arm_c_fixed_status.repetitions = ARM_CF_REPETITIONS;
    status = cf_init(&context, &c_fixed_tables);
    if ((arm_c_fixed_status.global_timer_control & UINT32_C(0xff01)) != 1U
        || arm_c_fixed_status.timer_hz_nominal == 0U
        || arm_c_fixed_status.timer_hz_nominal != arm_c_fixed_status.cpu_hz_nominal / 2U) {
        status = -101;
    }
    /* Accuracy gate and cache warmup are outside every measured interval. */
    for (i = 0; status == 0 && i < ARM_CF_WARMUPS; ++i) {
        status = cf_process(&context, c_fixed_smoke_re, c_fixed_smoke_im,
                            c_fixed_smoke_bfp_shift, &arm_c_fixed_workspace,
                            &arm_c_fixed_result, 0);
        if (status != 0) break;
        arm_c_fixed_status.mismatches = arm_cf_compare(&arm_c_fixed_result, &c_fixed_smoke_expected);
        if (arm_c_fixed_status.mismatches != 0U) break;
    }
    if (status == 0 && arm_c_fixed_status.mismatches == 0U) {
        for (i = 0; i < ARM_CF_REPETITIONS; ++i) {
            XTime first, last;
            XTime_GetTime(&first);
            status = cf_process(&context, c_fixed_smoke_re, c_fixed_smoke_im,
                                c_fixed_smoke_bfp_shift, &arm_c_fixed_workspace,
                                &arm_c_fixed_result, 0);
            XTime_GetTime(&last);
            arm_c_fixed_ticks[i] = (uint64_t)(last - first);
            /* Consume every output after stopping the timer; no LTO is used. */
            arm_c_fixed_status.checksum = arm_cf_checksum(&arm_c_fixed_result);
            arm_c_fixed_status.mismatches += arm_cf_compare(&arm_c_fixed_result, &c_fixed_smoke_expected);
            if (status != 0 || arm_c_fixed_status.mismatches != 0U || last <= first) {
                if (status == 0 && last <= first) status = -100;
                break;
            }
            arm_c_fixed_status.completed = i + 1U;
        }
    }
    arm_c_fixed_status.core_error = (uint32_t)status;
    Xil_DCacheFlushRange((INTPTR)arm_c_fixed_ticks, (u32)sizeof(arm_c_fixed_ticks));
    arm_cf_publish();
    for (i = 0; i < arm_c_fixed_status.completed; ++i) {
        xil_printf("TICKS %u ", i);
        arm_cf_hex64(arm_c_fixed_ticks[i]);
        xil_printf("\r\n");
    }
    xil_printf("TIMING completed=%u core_error=%u mismatches=%u checksum=%08x\r\n",
               (unsigned)arm_c_fixed_status.completed, (unsigned)arm_c_fixed_status.core_error,
               (unsigned)arm_c_fixed_status.mismatches, (unsigned)arm_c_fixed_status.checksum);
    arm_cf_finished();
    return status != 0 || arm_c_fixed_status.mismatches != 0U;
}
