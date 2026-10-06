#include "arm_c_fixed.h"
#include "c_fixed_tables.h"
#include "c_fixed_vectors.h"
#include "xil_cache.h"
#include "xil_printf.h"

cf_fft_trace arm_c_fixed_trace __attribute__((aligned(64)));
static cf_context context;

int main(void)
{
    int status;
    arm_cf_start(1U);
    status = cf_init(&context, &c_fixed_tables);
    if (status == 0) status = cf_process(&context, c_fixed_smoke_re, c_fixed_smoke_im,
                        c_fixed_smoke_bfp_shift, &arm_c_fixed_workspace,
                        &arm_c_fixed_result, &arm_c_fixed_trace);
    arm_c_fixed_status.core_error = (uint32_t)status;
    if (status == 0) {
        arm_c_fixed_status.mismatches = arm_cf_compare(&arm_c_fixed_result, &c_fixed_smoke_expected);
        arm_c_fixed_status.checksum = arm_cf_checksum(&arm_c_fixed_result);
        arm_cf_dump(&arm_c_fixed_result);
        arm_cf_dump_trace(&arm_c_fixed_trace);
    }
    arm_c_fixed_status.completed = 1U;
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_trace, (u32)sizeof(arm_c_fixed_trace));
    arm_cf_publish();
    xil_printf("VALIDATION core_error=%u mismatches=%u checksum=%08x\r\n",
               (unsigned)arm_c_fixed_status.core_error, (unsigned)arm_c_fixed_status.mismatches,
               (unsigned)arm_c_fixed_status.checksum);
    arm_cf_finished();
    return status != 0 || arm_c_fixed_status.mismatches != 0U;
}
