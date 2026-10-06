#include "mfcc_fp32_accel_xilinx.h"
#include "accel_smoke_vectors.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xtime_l.h"
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define ALIGNED64 __attribute__((aligned(64)))
#define DEMO_MAGIC UINT32_C(0x46504143)
#define DEMO_RECORD_CAPACITY ((1U+(MFCC_FP32_ACCEL_MAX_SAMPLES-512U)/160U)*13U)

typedef struct {
    uint32_t magic,version,mode,sample_count;
    uint32_t timeout_seconds,max_polls,pcm_crc32,reserved;
} accel_demo_control;
typedef struct {
    uint32_t magic,version,state;
    int32_t result;
    uint32_t numeric_checked,numeric_passed,failure_index,pcm_crc32;
    uint64_t timer_hz;
    mfcc_fp32_accel_stats transport;
} accel_demo_status;

_Static_assert(sizeof(accel_demo_control)==32, "DDR control ABI");
_Static_assert(sizeof(mfcc_fp32_accel_stats)==88, "DDR statistics ABI");
_Static_assert(sizeof(accel_demo_status)==128, "DDR result status ABI");
_Static_assert(offsetof(mfcc_fp32_accel_stats,core_error_detail)==84, "Core error detail ABI");
int16_t arm_fp32_accel_pcm[MFCC_FP32_ACCEL_MAX_SAMPLES] ALIGNED64;
mfcc_fp32_accel_record arm_fp32_accel_results[DEMO_RECORD_CAPACITY] ALIGNED64;
volatile accel_demo_control arm_fp32_accel_control ALIGNED64;
volatile accel_demo_status arm_fp32_accel_status ALIGNED64;
const uint32_t arm_fp32_accel_layout[16] ALIGNED64 = {
    DEMO_MAGIC,1,sizeof(accel_demo_control),sizeof(accel_demo_status),
    sizeof(mfcc_fp32_accel_record),sizeof(mfcc_fp32_accel_stats),
    offsetof(accel_demo_status,transport),MFCC_FP32_ACCEL_MAX_SAMPLES,
    DEMO_RECORD_CAPACITY,sizeof(int16_t),offsetof(mfcc_fp32_accel_record,frame),
    offsetof(mfcc_fp32_accel_record,index),offsetof(mfcc_fp32_accel_record,bfp_s),
    offsetof(mfcc_fp32_accel_record,flags),MFCC_FP32_ACCEL_BASE,MFCC_FP32_ACCEL_CONTRACT_TAG
};

static void publish(void)
{
    Xil_DCacheFlushRange((INTPTR)&arm_fp32_accel_status,(u32)sizeof(arm_fp32_accel_status));
    __asm__ volatile("dsb sy\n\tisb sy" ::: "memory");
}

static uint32_t pcm_crc(const int16_t *pcm, uint32_t samples)
{
    uint32_t crc=UINT32_MAX,i;
    for (i=0; i<samples; ++i) {
        uint32_t byte,word=(uint32_t)(uint16_t)pcm[i];
        for (byte=0; byte<2U; ++byte) {
            uint32_t bit;
            crc^=(word>>(8U*byte))&255U;
            for (bit=0; bit<8U; ++bit)
                crc=(crc>>1)^(UINT32_C(0xedb88320)&(0U-(crc&1U)));
        }
    }
    return crc^UINT32_MAX;
}

void __attribute__((noinline,used)) arm_fp32_accel_ready_breakpoint(void)
{
    __asm__ volatile("nop" ::: "memory");
}
void __attribute__((noinline,used)) arm_fp32_accel_result_breakpoint(void)
{
    __asm__ volatile("nop" ::: "memory");
}

int main(void)
{
    mfcc_fp32_accel_bus bus;
    mfcc_fp32_accel_arm_context context;
    mfcc_fp32_accel_limits limits;
    mfcc_fp32_accel_stats stats;
    accel_demo_control job;
    uint32_t i;
    int result=MFCC_FP32_ACCEL_OK;
    Xil_ICacheEnable(); Xil_DCacheEnable(); XTime_SetTime((XTime)0);
    mfcc_fp32_accel_arm_init(&bus,&context);
    memset((void *)&arm_fp32_accel_status,0,sizeof(arm_fp32_accel_status));
    arm_fp32_accel_status.magic=DEMO_MAGIC; arm_fp32_accel_status.version=1;
    arm_fp32_accel_status.failure_index=UINT32_MAX;
    arm_fp32_accel_status.timer_hz=mfcc_fp32_accel_arm_ticks_per_second();
    for (i=0; i<512U; ++i) arm_fp32_accel_pcm[i]=accel_smoke_pcm[i];
    arm_fp32_accel_control.magic=DEMO_MAGIC; arm_fp32_accel_control.version=1;
    arm_fp32_accel_control.mode=0; arm_fp32_accel_control.sample_count=512;
    arm_fp32_accel_control.timeout_seconds=10; arm_fp32_accel_control.max_polls=UINT32_C(10000000);
    arm_fp32_accel_control.pcm_crc32=ACCEL_SMOKE_PCM_CRC32; arm_fp32_accel_control.reserved=0;
    /* Clean all potential JTAG input lines before the ready breakpoint. */
    Xil_DCacheFlushRange((INTPTR)arm_fp32_accel_pcm,(u32)sizeof(arm_fp32_accel_pcm));
    Xil_DCacheInvalidateRange((INTPTR)arm_fp32_accel_pcm,(u32)sizeof(arm_fp32_accel_pcm));
    Xil_DCacheFlushRange((INTPTR)&arm_fp32_accel_control,(u32)sizeof(arm_fp32_accel_control));
    Xil_DCacheFlushRange((INTPTR)arm_fp32_accel_layout,(u32)sizeof(arm_fp32_accel_layout));
    arm_fp32_accel_status.state=1; publish();
    xil_printf("ARM_FP32_ACCEL_READY_V1\r\n");
    arm_fp32_accel_ready_breakpoint();
    /* A debugger may replace control and PCM while halted at ready. */
    Xil_DCacheInvalidateRange((INTPTR)&arm_fp32_accel_control,(u32)sizeof(arm_fp32_accel_control));
    job=arm_fp32_accel_control;
    if (job.magic!=DEMO_MAGIC || job.version!=1U || job.mode>1U
        || job.sample_count>MFCC_FP32_ACCEL_MAX_SAMPLES || job.reserved!=0U
        || job.timeout_seconds==0U || job.timeout_seconds>60U || job.max_polls==0U
        || (job.mode==0U && job.sample_count!=512U)) result=-100;
    if (result==0) {
        Xil_DCacheInvalidateRange((INTPTR)arm_fp32_accel_pcm,(u32)(job.sample_count*sizeof(int16_t)));
        arm_fp32_accel_status.pcm_crc32=pcm_crc(arm_fp32_accel_pcm,job.sample_count);
        if (arm_fp32_accel_status.pcm_crc32!=job.pcm_crc32) result=-101;
    }
    if (result==0) {
        arm_fp32_accel_status.state=2; publish();
        memset(&stats,0,sizeof(stats));
        limits.timeout_ticks=mfcc_fp32_accel_arm_ticks_per_second()*(uint64_t)job.timeout_seconds;
        limits.max_polls=job.max_polls;
        result=mfcc_fp32_accel_run(&bus,arm_fp32_accel_pcm,job.sample_count,arm_fp32_accel_results,
                             DEMO_RECORD_CAPACITY,&limits,&stats);
        arm_fp32_accel_status.transport=stats;
        if (result==0 && job.mode==0U) {
            arm_fp32_accel_status.numeric_checked=1;
            for (i=0; i<13U; ++i) {
                if (arm_fp32_accel_results[i].value_bits!=accel_smoke_expected[i]
                    || arm_fp32_accel_results[i].bfp_s!=ACCEL_SMOKE_BFP_S) {
                    arm_fp32_accel_status.failure_index=i; result=-102; break;
                }
            }
            arm_fp32_accel_status.numeric_passed=(uint32_t)(result==0);
        }
        Xil_DCacheFlushRange((INTPTR)arm_fp32_accel_results,
                            (u32)(stats.records_received*sizeof(mfcc_fp32_accel_record)));
    }
    arm_fp32_accel_status.result=(int32_t)result;
    arm_fp32_accel_status.state=result==0 ? 3U : 4U;
    publish();
    xil_printf("ARM_FP32_ACCEL_RESULT_V1 state=%u result=%d records=%u\r\n",
               arm_fp32_accel_status.state,result,arm_fp32_accel_status.transport.records_received);
    arm_fp32_accel_result_breakpoint();
    for (;;) __asm__ volatile("nop" ::: "memory");
}
