#include "mfcc_dma_xilinx.h"
#include "dma_vectors.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xtime_l.h"
#include <stddef.h>
#include <string.h>

#define ALIGN64 __attribute__((aligned(64)))
#define MAGIC UINT32_C(0x444d4143)
#define DEV_SAMPLES 85920U
#define DEV_RECORDS 6942U
#define TRIALS 33U
#ifndef MFCC_DMA_CORE_ID
#error "Select the frozen fixed(1) or FP32(2) core at build time"
#endif
typedef struct {
 uint32_t magic,version,mode,samples,timeout_ms,warmups,repeats,pcm_crc32,sequence,reserved[3];
} demo_control;
typedef struct {
 uint32_t magic,version,state; int32_t result;
 uint32_t mode,sequence,samples,frames,records,completed,warmups,repeats,pcm_crc32;
 uint32_t numeric_checked,numeric_mismatches,core_id;
 mfcc_dma_stats transport;
} demo_status;
typedef struct { mfcc_dma_stats transport; int32_t result; uint32_t mismatches,discarded,sequence; } demo_trial;
_Static_assert(sizeof(demo_control)==48 && sizeof(demo_status)==216 && sizeof(demo_trial)==168,"DDR ABI");
_Static_assert(offsetof(demo_status,transport)==64,"Status transport offset");
int16_t arm_dma_pcm[MFCC_DMA_MAX_SAMPLES] ALIGN64;
mfcc_dma_record arm_dma_results[MFCC_DMA_MAX_RECORDS] ALIGN64;
mfcc_dma_record arm_dma_history[TRIALS][DEV_RECORDS] ALIGN64;
demo_trial arm_dma_trials[TRIALS] ALIGN64;
volatile demo_control arm_dma_control ALIGN64;
volatile demo_status arm_dma_status ALIGN64;
const uint32_t arm_dma_layout[32] ALIGN64={
 MAGIC,2,sizeof(demo_control),sizeof(demo_status),sizeof(mfcc_dma_record),sizeof(mfcc_dma_stats),
 offsetof(demo_status,transport),MFCC_DMA_MAX_SAMPLES,MFCC_DMA_MAX_RECORDS,2,
 offsetof(mfcc_dma_record,frame),offsetof(mfcc_dma_record,index),offsetof(mfcc_dma_record,bfp),offsetof(mfcc_dma_record,flags),
 MFCC_DMA_BASE,MFCC_DMA_ENGINE_BASE,MFCC_DMA_CORE_ID,UINT32_C(0x20000),
 DEV_SAMPLES,DEV_RECORDS,TRIALS,sizeof(demo_trial),sizeof(arm_dma_history[0]),
 (uint32_t)COUNTS_PER_SECOND,61,62,63,29,UINT32_C(0x444d4143),0,0,0
};
static void publish(void)
{ Xil_DCacheFlushRange((INTPTR)&arm_dma_status,(u32)sizeof(arm_dma_status));__asm__ volatile("dsb sy\n\tisb sy":::"memory"); }
static uint32_t crc_pcm(uint32_t samples)
{
 uint32_t crc=UINT32_MAX,i;
 for(i=0;i<samples;++i) {
  uint32_t j,v=(uint32_t)(uint16_t)arm_dma_pcm[i];
  for(j=0;j<2U;++j) {uint32_t k;crc^=(v>>(8U*j))&255U;for(k=0;k<8U;++k)crc=(crc>>1)^(UINT32_C(0xedb88320)&(0U-(crc&1U)));}
 }
 return crc^UINT32_MAX;
}
void __attribute__((noinline,used)) arm_dma_ready_breakpoint(void) { __asm__ volatile("nop":::"memory"); }
void __attribute__((noinline,used)) arm_dma_result_breakpoint(void) { __asm__ volatile("nop":::"memory"); }
static uint32_t compare(uint32_t records)
{
 uint32_t i,bad=0;
 for(i=0;i<records;++i)if(memcmp(&arm_dma_results[i],&dma_expected[i],sizeof(mfcc_dma_record))!=0)++bad;
 return bad;
}
int main(void)
{
 mfcc_dma_ops ops;mfcc_dma_arm context;uint32_t i,last_sequence=0,smoke_passed=0,full_passed=0;
 int initial;
 Xil_ICacheEnable();Xil_DCacheEnable();XTime_SetTime((XTime)0);
 memset(&ops,0,sizeof(ops));initial=mfcc_dma_arm_init(&ops,&context,MFCC_DMA_CORE_ID);
 for(i=0;i<512U;++i)arm_dma_pcm[i]=dma_smoke_pcm[i];
 memset((void *)&arm_dma_control,0,sizeof(arm_dma_control));
 arm_dma_control.magic=MAGIC;arm_dma_control.version=2;arm_dma_control.samples=512;
 arm_dma_control.timeout_ms=10000;arm_dma_control.repeats=1;arm_dma_control.sequence=1;
 arm_dma_control.pcm_crc32=DMA_SMOKE_CRC;
 Xil_DCacheFlushRange((INTPTR)arm_dma_layout,(u32)sizeof(arm_dma_layout));
 for(;;) {
  demo_control job;uint32_t calls=1,records=0;int result=initial;
  memset((void *)&arm_dma_status,0,sizeof(arm_dma_status));
  arm_dma_status.magic=MAGIC;arm_dma_status.version=2;arm_dma_status.core_id=MFCC_DMA_CORE_ID;arm_dma_status.result=(int32_t)initial;
  /* Clean/invalidate JTAG-owned inputs before each READY. Firmware owns no DMA
   * ranges here. Per-transaction cache work is separately timed by the driver. */
  Xil_DCacheFlushRange((INTPTR)arm_dma_pcm,(u32)sizeof(arm_dma_pcm));
  Xil_DCacheInvalidateRange((INTPTR)arm_dma_pcm,(u32)sizeof(arm_dma_pcm));
  Xil_DCacheFlushRange((INTPTR)&arm_dma_control,(u32)sizeof(arm_dma_control));
  arm_dma_status.state=1;publish();xil_printf("ARM_DMA_READY_V2 core=%u\r\n",MFCC_DMA_CORE_ID);
  arm_dma_ready_breakpoint();
  Xil_DCacheInvalidateRange((INTPTR)&arm_dma_control,(u32)sizeof(arm_dma_control));job=arm_dma_control;
  if(result==0 && (job.magic!=MAGIC || job.version!=2U || job.mode>2U || job.sequence!=last_sequence+1U
   || job.timeout_ms==0U || job.timeout_ms>60000U || job.reserved[0]!=0U || job.reserved[1]!=0U || job.reserved[2]!=0U
   || job.samples!=(job.mode==0U?512U:DEV_SAMPLES)
   || job.warmups!=(job.mode==2U?3U:0U) || job.repeats!=(job.mode==2U?30U:1U)
   || (job.mode==1U && smoke_passed==0U) || (job.mode==2U && full_passed==0U)))result=-100;
  arm_dma_status.mode=job.mode;arm_dma_status.sequence=job.sequence;arm_dma_status.samples=job.samples;
  arm_dma_status.warmups=job.warmups;arm_dma_status.repeats=job.repeats;
  if(result==0) {
   records=mfcc_dma_frames(job.samples)*13U;arm_dma_status.frames=records/13U;arm_dma_status.records=records;
   Xil_DCacheInvalidateRange((INTPTR)arm_dma_pcm,(u32)(job.samples*2U));
   arm_dma_status.pcm_crc32=crc_pcm(job.samples);
   if(arm_dma_status.pcm_crc32!=job.pcm_crc32)result=-101;
  }
  if(result==0) {
   calls=job.warmups+job.repeats;arm_dma_status.state=2;publish();
   for(i=0;i<calls;++i) {
    mfcc_dma_stats stats;uint32_t mismatches=0;memset(&stats,0,sizeof(stats));
    result=mfcc_dma_run(&ops,MFCC_DMA_CORE_ID,arm_dma_pcm,job.samples,arm_dma_results,MFCC_DMA_MAX_RECORDS,job.timeout_ms,&stats);
    /* Numeric comparison, history copies and publication occur AFTER driver
     * elapsed_ticks is finalized. All24 bytes per record are compared. */
    if(result==0) {mismatches=compare(records);arm_dma_status.numeric_checked+=records;arm_dma_status.numeric_mismatches+=mismatches;if(mismatches!=0U)result=-102;}
    arm_dma_trials[i].transport=stats;arm_dma_trials[i].result=(int32_t)result;
    arm_dma_trials[i].mismatches=mismatches;arm_dma_trials[i].discarded=(uint32_t)(i<job.warmups);arm_dma_trials[i].sequence=i;
    arm_dma_status.transport=stats;arm_dma_status.completed=i+1U;
    if(job.mode==2U && result==0)memcpy(arm_dma_history[i],arm_dma_results,(size_t)records*sizeof(mfcc_dma_record));
    if(result!=0)break;
   }
   if(result==0)Xil_DCacheFlushRange((INTPTR)arm_dma_results,(u32)(records*sizeof(mfcc_dma_record)));
   Xil_DCacheFlushRange((INTPTR)arm_dma_trials,(u32)(arm_dma_status.completed*sizeof(demo_trial)));
   if(job.mode==2U)Xil_DCacheFlushRange((INTPTR)arm_dma_history,(u32)(arm_dma_status.completed*sizeof(arm_dma_history[0])));
  }
  arm_dma_status.result=(int32_t)result;arm_dma_status.state=result==0?3U:4U;publish();
  xil_printf("ARM_DMA_RESULT_V2 core=%u mode=%u result=%d completed=%u\r\n",MFCC_DMA_CORE_ID,job.mode,result,arm_dma_status.completed);
  arm_dma_result_breakpoint();
  if(result!=0)for(;;)__asm__ volatile("nop":::"memory"); /* no reuse after failure */
  if(job.mode==0U)smoke_passed=1;
  if(job.mode==1U)full_passed=1;
  last_sequence=job.sequence;
 }
}
