#include "arm_c_fixed.h"

#include "c_fixed_contract.h"
#include "arm_c_fixed_binding.h"
#include "xil_cache.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xtime_l.h"

#if !defined(__arm__) || !defined(__ARM_PCS_VFP)
#error "Use the Cortex-A9 hard-float ABI required by the reused standalone BSP"
#endif

#define CF_ALIGN __attribute__((aligned(64)))
volatile arm_cf_status arm_c_fixed_status CF_ALIGN;
cf_workspace arm_c_fixed_workspace CF_ALIGN;
cf_result arm_c_fixed_result CF_ALIGN;

static void barrier(void)
{
    __asm__ volatile("dsb sy\n\tisb sy" ::: "memory");
}

void arm_cf_hex64(uint64_t value)
{
    /* xil_printf does not provide a portable 64-bit conversion. */
    xil_printf("%08x%08x", (unsigned)(value >> 32), (unsigned)value);
}

void arm_cf_start(uint32_t mode)
{
    XTime first, last;
    arm_cf_status initial = {0};
    arm_c_fixed_status = initial;
    Xil_ICacheEnable();
    Xil_DCacheEnable();
    arm_c_fixed_status.magic = ARM_CF_MAGIC;
    arm_c_fixed_status.mode = mode;
#if defined(XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ)
    arm_c_fixed_status.cpu_hz_nominal = XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ;
#elif defined(XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ)
    arm_c_fixed_status.cpu_hz_nominal = XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ;
#endif
    arm_c_fixed_status.timer_hz_nominal = (uint32_t)COUNTS_PER_SECOND;
    __asm__ volatile("mrc p15, 0, %0, c1, c0, 0" : "=r"(arm_c_fixed_status.sctlr));
    __asm__ volatile("mrs %0, cpsr" : "=r"(arm_c_fixed_status.cpsr));
    arm_c_fixed_status.l2_cache_control = Xil_In32((UINTPTR)UINT32_C(0xf8f02100));
    arm_c_fixed_status.arm_clock_registers[0] = Xil_In32((UINTPTR)UINT32_C(0xf8000100));
    arm_c_fixed_status.arm_clock_registers[1] = Xil_In32((UINTPTR)UINT32_C(0xf8000104));
    arm_c_fixed_status.arm_clock_registers[2] = Xil_In32((UINTPTR)UINT32_C(0xf8000108));
    arm_c_fixed_status.arm_clock_registers[3] = Xil_In32((UINTPTR)UINT32_C(0xf8000120));
    arm_c_fixed_status.workspace_bytes = sizeof(cf_workspace);
    arm_c_fixed_status.result_bytes = sizeof(cf_result);
    arm_c_fixed_status.trace_bytes = sizeof(cf_fft_trace);
    arm_c_fixed_status.tables_bytes = sizeof(cf_tables);
    XTime_SetTime((XTime)0);
    arm_c_fixed_status.global_timer_control = Xil_In32((UINTPTR)GLOBAL_TMR_BASEADDR + GTIMER_CONTROL_OFFSET);
    XTime_GetTime(&first);
    XTime_GetTime(&last);
    arm_c_fixed_status.timer_read_pair_ticks = (uint64_t)(last - first);
    xil_printf("C_FIXED_PARTIAL_V1 mode=%u N=512 bins=257 mel=26\r\n", (unsigned)mode);
    xil_printf("contract=%s sha256=%s\r\n", C_FIXED_CONTRACT_VERSION, C_FIXED_CONTRACT_SHA256);
    xil_printf("FRAME case=%s frame_id=%u start_sample=%u pcm_sha256=%s\r\n",
               ARM_CF_SMOKE_CASE, (unsigned)ARM_CF_SMOKE_FRAME_ID,
               (unsigned)ARM_CF_SMOKE_START_SAMPLE, ARM_CF_SMOKE_PCM_SHA256);
    xil_printf("cpu_hz_nominal=%u timer_hz_nominal=%u sctlr=%08x\r\n",
               (unsigned)arm_c_fixed_status.cpu_hz_nominal,
               (unsigned)arm_c_fixed_status.timer_hz_nominal,
               (unsigned)arm_c_fixed_status.sctlr);
}

uint32_t arm_cf_compare(const cf_result *a, const cf_result *b)
{
    uint32_t bad = 0;
    unsigned i;
    for (i = 0; i < 512U; ++i) {
        bad += a->fft_re[i] != b->fft_re[i];
        bad += a->fft_im[i] != b->fft_im[i];
    }
    for (i = 0; i < 257U; ++i) bad += a->power[i] != b->power[i];
    for (i = 0; i < 26U; ++i) bad += a->mel[i] != b->mel[i];
    bad += a->bfp_shift != b->bfp_shift;
    bad += a->power_exp2 != b->power_exp2;
    bad += a->mel_exp2 != b->mel_exp2;
    bad += a->fft_overflow != b->fft_overflow;
    bad += a->power_overflow != b->power_overflow;
    bad += a->mel_overflow != b->mel_overflow;
    return bad;
}

static uint32_t mix(uint32_t sum, uint64_t value)
{
    sum = ((sum << 5) | (sum >> 27)) ^ (uint32_t)value;
    return sum + (uint32_t)(value >> 32) + UINT32_C(0x9e3779b9);
}

uint32_t arm_cf_checksum(const cf_result *r)
{
    uint32_t sum = ARM_CF_MAGIC;
    unsigned i;
    for (i = 0; i < 512U; ++i) {
        sum = mix(sum, (uint32_t)r->fft_re[i]);
        sum = mix(sum, (uint32_t)r->fft_im[i]);
    }
    for (i = 0; i < 257U; ++i) sum = mix(sum, r->power[i]);
    for (i = 0; i < 26U; ++i) sum = mix(sum, r->mel[i]);
    sum = mix(sum, (uint32_t)r->bfp_shift);
    sum = mix(sum, (uint32_t)r->power_exp2);
    sum = mix(sum, (uint32_t)r->mel_exp2);
    sum = mix(sum, r->fft_overflow);
    sum = mix(sum, r->power_overflow);
    return mix(sum, r->mel_overflow);
}

void arm_cf_publish(void)
{
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_result, (u32)sizeof(arm_c_fixed_result));
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_status, (u32)sizeof(arm_c_fixed_status));
    barrier();
}

void arm_cf_dump(const cf_result *r)
{
    unsigned i;
    xil_printf("META s=%d power_exp2=%d mel_exp2=%d fft_ovf=%u power_ovf=%u mel_ovf=%u\r\n",
               (int)r->bfp_shift, (int)r->power_exp2, (int)r->mel_exp2,
               (unsigned)r->fft_overflow, (unsigned)r->power_overflow, (unsigned)r->mel_overflow);
    for (i = 0; i < 512U; ++i)
        xil_printf("FFT %u %d %d\r\n", i, (int)r->fft_re[i], (int)r->fft_im[i]);
    for (i = 0; i < 257U; ++i) {
        xil_printf("POWER %u ", i);
        arm_cf_hex64(r->power[i]);
        xil_printf("\r\n");
    }
    for (i = 0; i < 26U; ++i) {
        xil_printf("MEL %u ", i);
        arm_cf_hex64(r->mel[i]);
        xil_printf("\r\n");
    }
}

void arm_cf_dump_trace(const cf_fft_trace *trace)
{
    unsigned group, i;
    for (i = 0; i < 512U; ++i)
        xil_printf("PROMOTED %u %d %d\r\n", i, (int)trace->promoted[0][i], (int)trace->promoted[1][i]);
    for (group = 0; group < 5U; ++group) {
        xil_printf("GROUP_OVF %u %u\r\n", group, (unsigned)trace->overflow_after_group[group]);
        for (i = 0; i < 512U; ++i)
            xil_printf("GROUP %u %u %d %d\r\n", group, i,
                       (int)trace->groups[group][0][i], (int)trace->groups[group][1][i]);
    }
}

/* A debugger can break here and read exported symbols after cache flush.
 * This is not a hardware-cycle emulation wait in the computation core. */
void __attribute__((noinline, used)) arm_cf_finished(void)
{
    __asm__ volatile("nop" ::: "memory");
}
