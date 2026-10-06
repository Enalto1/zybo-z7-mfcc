#include "mfcc_fp32_accel_xilinx.h"
#include "xil_io.h"
#include "xil_mmu.h"
#include "xtime_l.h"

#if !defined(__arm__) || !defined(__ARM_PCS_VFP)
#error "Use the standalone Cortex-A9 hard-float BSP ABI"
#endif
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "The transport requires little-endian ARM"
#endif
_Static_assert(sizeof(uintptr_t)==4, "Cortex-A9 address width");

/* ARMv7 short-descriptor section XN is bit4. Cortex-A9 ID_MMFR0.VMSA=3
 * does not implement PXN, so bit0 must remain zero (descriptor type 0b10).
 * This BSP's EXECUTE_NEVER macro also sets bit0; using it produced a section
 * translation fault before the first accelerator read on the physical A9.
 * ARM DDI0406C.d B3.5, p.B3-1324; ID_MMFR0, p.B4-1616.
 */
#define ARM_A9_SECTION_XN UINT32_C(0x10)
_Static_assert(((DEVICE_MEMORY|ARM_A9_SECTION_XN)&UINT32_C(3))==UINT32_C(2),
               "Cortex-A9 MMIO must use a section descriptor without PXN");

static void barrier(void)
{
    __asm__ volatile("dsb sy" ::: "memory");
}

static int mmio_read(void *opaque, uint32_t offset, uint32_t *value)
{
    const mfcc_fp32_accel_arm_context *context=(const mfcc_fp32_accel_arm_context *)opaque;
    if (offset>MFCC_R_CORE_ERROR_DETAIL || (offset&3U)!=0U) return -1;
    barrier();
    /* A bus SLVERR may raise an ARM data abort. This adapter does not install
       an exception handler and cannot turn that exception into a return code. */
    *value=Xil_In32((UINTPTR)context->base_address+(UINTPTR)offset);
    barrier();
    return 0;
}

static int mmio_write(void *opaque, uint32_t offset, uint32_t value)
{
    const mfcc_fp32_accel_arm_context *context=(const mfcc_fp32_accel_arm_context *)opaque;
    if (offset>MFCC_R_CORE_ERROR_DETAIL || (offset&3U)!=0U) return -1;
    barrier();
    Xil_Out32((UINTPTR)context->base_address+(UINTPTR)offset,value);
    barrier();
    return 0;
}

static uint64_t arm_ticks(void *opaque)
{
    XTime value;
    (void)opaque;
    XTime_GetTime(&value);
    return (uint64_t)value;
}

void mfcc_fp32_accel_arm_init(mfcc_fp32_accel_bus *bus, mfcc_fp32_accel_arm_context *context)
{
    context->base_address=MFCC_FP32_ACCEL_BASE;
    /* One MiB MMU section containing only the designated PL MMIO aperture.
     * PCM/results remain normal DDR; there is no DMA cache-ownership transfer.
     */
    Xil_SetTlbAttributes((INTPTR)MFCC_FP32_ACCEL_BASE,DEVICE_MEMORY|ARM_A9_SECTION_XN);
    barrier();
    __asm__ volatile("isb sy" ::: "memory");
    bus->context=context; bus->read32=mmio_read; bus->write32=mmio_write;
    bus->ticks=arm_ticks;
}

uint64_t mfcc_fp32_accel_arm_ticks_per_second(void)
{
    return (uint64_t)COUNTS_PER_SECOND;
}
