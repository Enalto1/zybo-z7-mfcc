#include "arm_c_fixed_full.h"
#include "c_fixed_contract.h"
#include "c_fixed_tables.h"
#include "c_fixed_full_tables.h"
#include "arm_c_fixed_full_binding.h"

#include "xil_cache.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xtime_l.h"

#if !defined(__arm__) || !defined(__ARM_PCS_VFP)
#error "Requires Cortex-A9 and the reused BSP hard-float ABI"
#endif

#define FULL_ALIGN __attribute__((aligned(64)))
volatile arm_cf_full_status arm_c_fixed_full_status FULL_ALIGN;
arm_cf_full_record arm_c_fixed_full_results[C_FIXED_FULL_DEV_FRAMES] FULL_ALIGN;
cf_full_state arm_c_fixed_full_state FULL_ALIGN;
cf_full_workspace arm_c_fixed_full_workspace FULL_ALIGN;
cf_full_output arm_c_fixed_full_frame FULL_ALIGN;
static cf_full_context full_context;

void arm_cf_full_hex64(uint64_t value)
{
    xil_printf("%08x%08x", (unsigned)(value >> 32), (unsigned)value);
}

void arm_cf_full_start(uint32_t mode)
{
    arm_cf_full_status empty = {0};
    XTime first, last;
    arm_c_fixed_full_status = empty;
    Xil_ICacheEnable();
    Xil_DCacheEnable();
    arm_c_fixed_full_status.magic = ARM_CF_FULL_MAGIC;
    arm_c_fixed_full_status.mode = mode;
    arm_c_fixed_full_status.samples = C_FIXED_FULL_DEV_SAMPLES;
#if defined(XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ)
    arm_c_fixed_full_status.cpu_hz_nominal = XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ;
#elif defined(XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ)
    arm_c_fixed_full_status.cpu_hz_nominal = XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ;
#endif
    arm_c_fixed_full_status.timer_hz_nominal = (uint32_t)COUNTS_PER_SECOND;
    __asm__ volatile("mrc p15, 0, %0, c1, c0, 0" : "=r"(arm_c_fixed_full_status.sctlr));
    __asm__ volatile("mrs %0, cpsr" : "=r"(arm_c_fixed_full_status.cpsr));
    arm_c_fixed_full_status.l2_cache_control = Xil_In32((UINTPTR)UINT32_C(0xf8f02100));
    arm_c_fixed_full_status.arm_clock_registers[0] = Xil_In32((UINTPTR)UINT32_C(0xf8000100));
    arm_c_fixed_full_status.arm_clock_registers[1] = Xil_In32((UINTPTR)UINT32_C(0xf8000104));
    arm_c_fixed_full_status.arm_clock_registers[2] = Xil_In32((UINTPTR)UINT32_C(0xf8000108));
    arm_c_fixed_full_status.arm_clock_registers[3] = Xil_In32((UINTPTR)UINT32_C(0xf8000120));
    arm_c_fixed_full_status.state_bytes = sizeof(cf_full_state);
    arm_c_fixed_full_status.workspace_bytes = sizeof(cf_full_workspace);
    arm_c_fixed_full_status.frame_bytes = sizeof(cf_full_output);
    arm_c_fixed_full_status.context_bytes = sizeof(cf_full_context);
    arm_c_fixed_full_status.spectral_tables_bytes = sizeof(cf_tables);
    arm_c_fixed_full_status.full_tables_bytes = sizeof(cf_full_tables);
    XTime_SetTime((XTime)0);
    arm_c_fixed_full_status.timer_control = Xil_In32((UINTPTR)GLOBAL_TMR_BASEADDR + GTIMER_CONTROL_OFFSET);
    XTime_GetTime(&first);
    XTime_GetTime(&last);
    arm_c_fixed_full_status.timer_read_pair_ticks = (uint64_t)(last - first);
    xil_printf("C_FIXED_FULL_V2 mode=%u samples=%u frames=%u\r\n", (unsigned)mode,
               (unsigned)C_FIXED_FULL_DEV_SAMPLES, (unsigned)C_FIXED_FULL_DEV_FRAMES);
    xil_printf("version=%s fixture_sha256=%s\r\n", C_FIXED_CONTRACT_VERSION, C_FIXED_CONTRACT_SHA256);
    xil_printf("published_contract_sha256=%s\r\n", C_FIXED_PUBLISHED_CONTRACT_SHA256);
    xil_printf("case=%s pcm_sha256=%s\r\n", ARM_CF_FULL_CASE, ARM_CF_FULL_PCM_SHA256);
    xil_printf("cpu_hz_nominal=%u timer_hz_nominal=%u sctlr=%08x\r\n",
               (unsigned)arm_c_fixed_full_status.cpu_hz_nominal,
               (unsigned)arm_c_fixed_full_status.timer_hz_nominal,
               (unsigned)arm_c_fixed_full_status.sctlr);
}

int arm_cf_full_initialize(void)
{
    return (int)cf_full_init(&full_context, &c_fixed_tables, &c_fixed_full_tables);
}

/* No UART, timer, allocation, cache maintenance or checksum in this loop.
 * State reset and final raw13/metadata stores are part of the measured job. */
int arm_cf_full_process(int32_t *preemphasis, uint32_t trace_frame, cf_full_output *trace)
{
    uint32_t sample, count = 0;
    int reset_status = (int)cf_full_reset(&arm_c_fixed_full_state);
    if (reset_status != 0) return reset_status;
    for (sample = 0; sample < C_FIXED_FULL_DEV_SAMPLES; ++sample) {
        uint32_t produced = 0;
        int error = (int)cf_full_push(&full_context, &arm_c_fixed_full_state,
            c_fixed_full_dev_pcm[sample], &arm_c_fixed_full_workspace,
            &arm_c_fixed_full_frame, &produced, preemphasis != 0 ? &preemphasis[sample] : 0);
        if (error != 0) return error;
        if (produced != 0) {
            cf_full_output *f = &arm_c_fixed_full_frame;
            arm_cf_full_record *record;
            unsigned c;
            if (count >= C_FIXED_FULL_DEV_FRAMES || f->frame_id != count
                || f->start_sample != (uint64_t)count * 160U
                || f->spectral.power_overflow != 0U || f->spectral.mel_overflow != 0U)
                return -201;
            record = &arm_c_fixed_full_results[count];
            for (c = 0; c < 13U; ++c) record->raw13[c] = f->mfcc_q24[c];
            record->metadata[0] = (int32_t)count;
            record->metadata[1] = (int32_t)(count * 160U);
            record->metadata[2] = f->spectral.bfp_shift;
            record->metadata[3] = f->spectral.power_exp2;
            record->metadata[4] = f->spectral.mel_exp2;
            record->metadata[5] = (int32_t)f->spectral.fft_overflow;
            record->metadata[6] = (int32_t)f->input_clips;
            record->metadata[7] = (int32_t)f->bfp_clamped;
            if (trace != 0 && count == trace_frame) *trace = *f;
            ++count;
        }
    }
    {
        int finish_status = (int)cf_full_finish(&arm_c_fixed_full_state);
        if (finish_status != 0) return finish_status;
    }
    if (arm_c_fixed_full_state.samples_seen != C_FIXED_FULL_DEV_SAMPLES
        || arm_c_fixed_full_state.frames_emitted != count
        || arm_c_fixed_full_state.previous_pcm != c_fixed_full_dev_pcm[C_FIXED_FULL_DEV_SAMPLES - 1U])
        return -203;
    arm_c_fixed_full_status.frames = count;
    return count == C_FIXED_FULL_DEV_FRAMES ? 0 : -202;
}

uint32_t arm_cf_full_compare(void)
{
    uint32_t frame, bad = 0;
    unsigned c;
    for (frame = 0; frame < C_FIXED_FULL_DEV_FRAMES; ++frame) {
        for (c = 0; c < 13U; ++c)
            bad += arm_c_fixed_full_results[frame].raw13[c] != c_fixed_full_dev_mfcc[frame][c];
        for (c = 0; c < 8U; ++c)
            bad += arm_c_fixed_full_results[frame].metadata[c] != c_fixed_full_dev_metadata[frame][c];
    }
    return bad;
}

static uint32_t mix(uint32_t sum, uint64_t value)
{
    return (((sum << 5) | (sum >> 27)) ^ (uint32_t)value)
        + (uint32_t)(value >> 32) + UINT32_C(0x9e3779b9);
}

uint32_t arm_cf_full_checksum(void)
{
    uint32_t frame, sum = ARM_CF_FULL_MAGIC;
    unsigned c;
    for (frame = 0; frame < C_FIXED_FULL_DEV_FRAMES; ++frame) {
        for (c = 0; c < 13U; ++c) sum = mix(sum, (uint64_t)arm_c_fixed_full_results[frame].raw13[c]);
        for (c = 0; c < 8U; ++c) sum = mix(sum, (uint32_t)arm_c_fixed_full_results[frame].metadata[c]);
    }
    return sum;
}

void arm_cf_full_dump_results(void)
{
    uint32_t frame;
    unsigned c;
    for (frame = 0; frame < C_FIXED_FULL_DEV_FRAMES; ++frame) {
        xil_printf("META %u", (unsigned)frame);
        for (c = 0; c < 8U; ++c) xil_printf(" %d", (int)arm_c_fixed_full_results[frame].metadata[c]);
        xil_printf("\r\n");
        for (c = 0; c < 13U; ++c) {
            xil_printf("RAW13 %u %u ", (unsigned)frame, c);
            arm_cf_full_hex64((uint64_t)arm_c_fixed_full_results[frame].raw13[c]);
            xil_printf("\r\n");
        }
    }
}

void arm_cf_full_dump_trace(const cf_full_output *f, const int32_t *pre)
{
    unsigned i;
    uint32_t start = (uint32_t)f->start_sample;
    xil_printf("TRACE frame=%u start=%u\r\n", (unsigned)f->frame_id, (unsigned)start);
    for (i = 0; i < 512U; ++i) {
        xil_printf("PRE %u %d\r\n", (unsigned)(start + i), (int)pre[start + i]);
        xil_printf("WINDOW %u %d\r\n", i, (int)f->windowed_q30[i]);
        xil_printf("FFT_INPUT %u %d\r\n", i, (int)f->fft_input[i]);
        xil_printf("FFT %u %d %d\r\n", i, (int)f->spectral.fft_re[i], (int)f->spectral.fft_im[i]);
    }
    for (i = 0; i < 257U; ++i) {
        xil_printf("POWER %u ", i);
        arm_cf_full_hex64(f->spectral.power[i]);
        xil_printf("\r\n");
    }
    for (i = 0; i < 26U; ++i) {
        xil_printf("MEL %u ", i);
        arm_cf_full_hex64(f->spectral.mel[i]);
        xil_printf(" LOG %d FLOOR %u\r\n", (int)f->log_q24[i], (unsigned)f->floor[i]);
    }
}

void arm_cf_full_publish(void)
{
    Xil_DCacheFlushRange((INTPTR)arm_c_fixed_full_results, (u32)sizeof(arm_c_fixed_full_results));
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_full_status, (u32)sizeof(arm_c_fixed_full_status));
    Xil_DCacheFlushRange((INTPTR)&arm_c_fixed_full_state, (u32)sizeof(arm_c_fixed_full_state));
    __asm__ volatile("dsb sy\n\tisb sy" ::: "memory");
}

void __attribute__((noinline, used)) arm_cf_full_ready(void)
{
    __asm__ volatile("nop" ::: "memory");
}

void __attribute__((noinline, used)) arm_cf_full_finished(void)
{
    __asm__ volatile("nop" ::: "memory");
}
