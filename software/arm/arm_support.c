#include "arm_support.h"

#include "xil_cache.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xtime_l.h"

#if !defined(__arm__) || !defined(__ARM_PCS_VFP)
#error "Build the adapter for 32-bit ARM with the hard-float calling convention"
#endif
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "The debugger protocol requires a little-endian ARM target"
#endif

#define ALIGNED64 __attribute__((aligned(64)))
#define FPSCR_REQUIRED_ZERO ((UINT32_C(3) << 22) | (UINT32_C(1) << 24) | (UINT32_C(1) << 25))

volatile arm_control_block arm_control ALIGNED64;
volatile arm_status_block arm_status ALIGNED64;
volatile uint32_t arm_clock_registers[4] ALIGNED64;
/* This is the only memory written by the startup DDR test. Linker reserves it. */
volatile uint32_t arm_ddr_test[ARM_DDR_TEST_WORDS]
    __attribute__((section(".ddr_test"), aligned(4096), used));

const arm_layout_descriptor arm_layout ALIGNED64 = {
    ARM_LAYOUT_MAGIC, ARM_PROTOCOL_VERSION,
    sizeof(arm_control_block), sizeof(arm_status_block),
    ARM_PCM_CAPACITY, ARM_OUTPUT_CAPACITY, sizeof(arm_output_record), sizeof(mfcc_frame),
    offsetof(mfcc_frame, start_sample), offsetof(mfcc_frame, frame_id),
    offsetof(mfcc_frame, frames), offsetof(mfcc_frame, windowed), offsetof(mfcc_frame, fft),
    offsetof(mfcc_frame, power), offsetof(mfcc_frame, mel_energies), offsetof(mfcc_frame, log_mel),
    offsetof(mfcc_frame, dct), offsetof(mfcc_frame, mfcc), offsetof(mfcc_frame, frame_energy),
    MFCC_FRAME_LENGTH, MFCC_NBINS, MFCC_NMEL, MFCC_NCEPS,
    sizeof(float), sizeof(uint64_t), ARM_TIMING_CAPACITY, sizeof(arm_timing_record),
    {0, 0, 0, 0, 0}
};

static void memory_barrier(void)
{
    __asm__ volatile("dsb sy\n\tisb sy" ::: "memory");
}

uint32_t arm_read_fpscr(void)
{
    uint32_t value;
    __asm__ volatile("vmrs %0, fpscr" : "=r"(value));
    return value;
}

static uint32_t read_sctlr(void)
{
    uint32_t value;
    __asm__ volatile("mrc p15, 0, %0, c1, c0, 0" : "=r"(value));
    return value;
}

static uint32_t read_cpsr(void)
{
    uint32_t value;
    __asm__ volatile("mrs %0, cpsr" : "=r"(value));
    return value;
}

void arm_flush(const void *address, size_t bytes)
{
    if (bytes != 0U) {
        Xil_DCacheFlushRange((INTPTR)address, (u32)bytes);
    }
    memory_barrier();
}

void arm_invalidate(const void *address, size_t bytes)
{
    if (bytes != 0U) {
        Xil_DCacheInvalidateRange((INTPTR)address, (u32)bytes);
    }
    memory_barrier();
}

uint32_t arm_crc32(const void *address, size_t bytes)
{
    const uint8_t *data = (const uint8_t *)address;
    uint32_t crc = UINT32_MAX;
    size_t index;
    for (index = 0; index < bytes; ++index) {
        uint32_t bit;
        crc ^= data[index];
        for (bit = 0; bit < 8U; ++bit) {
            uint32_t mask = 0U - (crc & 1U);
            crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & mask);
        }
    }
    return crc ^ UINT32_MAX;
}

static uint32_t ddr_pattern(uint32_t pass, uint32_t index)
{
    static const uint32_t constants[4] = {
        UINT32_C(0), UINT32_MAX, UINT32_C(0xa5a5a5a5), UINT32_C(0x5a5a5a5a)
    };
    if (pass < 4U) {
        return constants[pass];
    }
    return (uint32_t)(uintptr_t)&arm_ddr_test[index] ^ UINT32_C(0xa5a55a5a);
}

static int test_reserved_ddr(void)
{
    uint32_t pass;
    arm_status.ddr_test_passed = 0;
    arm_status.ddr_failure_index = UINT32_MAX;
    for (pass = 0; pass < 5U; ++pass) {
        uint32_t index;
        for (index = 0; index < ARM_DDR_TEST_WORDS; ++index) {
            arm_ddr_test[index] = ddr_pattern(pass, index);
        }
        arm_flush((const void *)arm_ddr_test, sizeof(arm_ddr_test));
        arm_invalidate((const void *)arm_ddr_test, sizeof(arm_ddr_test));
        for (index = 0; index < ARM_DDR_TEST_WORDS; ++index) {
            uint32_t expected = ddr_pattern(pass, index);
            uint32_t actual = arm_ddr_test[index];
            if (actual != expected) {
                arm_status.ddr_failure_index = index;
                arm_status.ddr_expected = expected;
                arm_status.ddr_actual = actual;
                return -1;
            }
        }
    }
    arm_status.ddr_test_passed = 1;
    return 0;
}

void arm_publish_status(void)
{
    arm_flush((const void *)&arm_status, sizeof(arm_status));
    arm_flush((const void *)arm_clock_registers, sizeof(arm_clock_registers));
}

int arm_startup(const char *marker)
{
    arm_control_block empty_control = {0};
    arm_status_block empty_status = {0};
    uint32_t fpscr;
    XTime before, after;
    arm_control = empty_control;
    arm_status = empty_status;
    arm_status.magic = ARM_STATUS_MAGIC;
    arm_status.version = ARM_PROTOCOL_VERSION;
    arm_status.state = ARM_STATE_BOOT;
    arm_status.control_bytes = sizeof(arm_control_block);
    arm_status.status_bytes = sizeof(arm_status_block);

    /* These deliberate cache settings and actual CP15 bits are recorded. */
    Xil_ICacheEnable();
    Xil_DCacheEnable();
    arm_status.fpscr_before = arm_read_fpscr();
    fpscr = arm_status.fpscr_before & ~FPSCR_REQUIRED_ZERO;
    __asm__ volatile("vmsr fpscr, %0" :: "r"(fpscr) : "memory");
    memory_barrier();
    arm_status.fpscr_active = arm_read_fpscr();
    arm_status.sctlr = read_sctlr();
    arm_status.cpsr = read_cpsr();
#if defined(XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ)
    arm_status.cpu_hz = XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ;
#elif defined(XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ)
    arm_status.cpu_hz = XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ;
#else
    arm_status.cpu_hz = 0; /* Unknown is retained, never inferred from a board name. */
#endif
    arm_status.timer_hz = (uint32_t)COUNTS_PER_SECOND;
    arm_clock_registers[0] = Xil_In32((UINTPTR)UINT32_C(0xf8000100));
    arm_clock_registers[1] = Xil_In32((UINTPTR)UINT32_C(0xf8000104));
    arm_clock_registers[2] = Xil_In32((UINTPTR)UINT32_C(0xf8000108));
    arm_clock_registers[3] = Xil_In32((UINTPTR)UINT32_C(0xf8000120));
    /* A CPU0-only standalone experiment owns the global timer. BSP SetTime
     * enables it with prescaler zero; no clock/time measurement is inferred.
     */
    XTime_SetTime((XTime)0);
    arm_status.global_timer_control = Xil_In32((UINTPTR)GLOBAL_TMR_BASEADDR + GTIMER_CONTROL_OFFSET);
    arm_status.l2_cache_control = Xil_In32((UINTPTR)UINT32_C(0xf8f02100));
    XTime_GetTime(&before);
    XTime_GetTime(&after);
    arm_status.timer_overhead_low = (uint32_t)(after - before);
    arm_status.timer_overhead_high = (uint32_t)((after - before) >> 32);
    /* Keep the descriptor visible even with function/data section GC enabled. */
    __asm__ volatile("" :: "r"(&arm_layout) : "memory");
    xil_printf("%s\r\n", marker);
    if ((arm_status.fpscr_active & FPSCR_REQUIRED_ZERO) != 0U) {
        arm_complete(ARM_ERROR_FPSCR);
        return -1;
    }
    if (test_reserved_ddr() != 0) {
        arm_complete(ARM_ERROR_DDR);
        return -1;
    }
    arm_status.state = ARM_STATE_READY;
    arm_flush((const void *)&arm_control, sizeof(arm_control));
    arm_publish_status();
    return 0;
}

void arm_complete(uint32_t error)
{
    arm_status.fpscr_after = arm_read_fpscr();
    if (error == ARM_ERROR_NONE && (arm_status.fpscr_after & FPSCR_REQUIRED_ZERO) != 0U) {
        error = ARM_ERROR_FPSCR;
    }
    arm_status.error = error;
    arm_status.state = error == ARM_ERROR_NONE ? ARM_STATE_DONE : ARM_STATE_ERROR;
    arm_publish_status();
    memory_barrier();
}

void __attribute__((noinline, used)) arm_ready_breakpoint(void)
{
    __asm__ volatile("nop" ::: "memory");
}

void __attribute__((noinline, used)) arm_result_breakpoint(void)
{
    __asm__ volatile("nop" ::: "memory");
}

void arm_idle(void)
{
    for (;;) {
        __asm__ volatile("nop" ::: "memory");
    }
}
