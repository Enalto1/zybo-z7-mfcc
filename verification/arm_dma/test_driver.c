#include "mfcc_dma.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_MSC_VER)
#define ALIGNED __declspec(align(64))
#else
#define ALIGNED __attribute__((aligned(64)))
#endif
static unsigned checks;
#define CHECK(x) do {++checks;if(!(x)){fprintf(stderr,"FAIL line=%d %s\n",__LINE__,#x);exit(1);}}while(0)
static ALIGNED int16_t pcm[MFCC_DMA_MAX_SAMPLES];
static ALIGNED mfcc_dma_record out[MFCC_DMA_MAX_RECORDS];
typedef struct {
 uint32_t reg[30],step,rx_step,start_step,tx_step,prepare_step,finish_step,recover_step,abort_step;
 uint32_t reads,writes,fail_read,fail_write,fail_stage,recovered,aborted,wait_events,wait_result;
 uint32_t corrupt_offset,corrupt_value,detail_reads,detail_before_failed_read;
 uint64_t now,origin,absolute_deadline;int recover_fail;
} fake;
static uint64_t fticks(void *p) {fake *f=p;return f->now++;}
static int fr(void *p,uint32_t off,uint32_t *v)
{
 fake *f=p;++f->reads;
 if(off==DMA_R_DETAIL)++f->detail_reads;
 if(off==f->fail_read){f->detail_before_failed_read=f->detail_reads;return -1;}
 *v=f->reg[off/4U];if(off==f->corrupt_offset)*v=f->corrupt_value;return 0;
}
static int fw(void *p,uint32_t off,uint32_t v)
{
 fake *f=p;++f->writes;++f->step;if(off==f->fail_write)return -1;
 if(off==DMA_R_CONTROL && v==1U)f->start_step=f->step;
 if(off==DMA_R_CONTROL && v==2U){++f->aborted;f->abort_step=f->step;f->reg[DMA_R_DETAIL/4U]=0;}
 if(off==DMA_R_SAMPLES)f->reg[off/4U]=v;return 0;
}
static int prep(void *p,mfcc_dma_stats *s,const void *x,uint32_t n,void *y,uint32_t m,uint64_t deadline)
{
 fake *f=p;f->prepare_step=++f->step;f->absolute_deadline=deadline;
 CHECK(x==pcm && y==out);CHECK(n==s->samples*2U && m==mfcc_dma_frames(s->samples)*13U*24U);
 return f->fail_stage==1U?-1:0;
}
static int rx(void *p,void *y,uint32_t n)
{fake *f=p;CHECK(y==out && n>0U);f->rx_step=++f->step;return f->fail_stage==2U?-1:0;}
static int tx(void *p,const void *x,uint32_t n)
{fake *f=p;CHECK(x==pcm && n>0U);f->tx_step=++f->step;return f->fail_stage==3U?-1:0;}
static int wait_callback(void *p,uint32_t required,mfcc_dma_stats *s)
{
 fake *f=p;uint32_t samples=f->reg[DMA_R_SAMPLES/4U],frames=mfcc_dma_frames(samples);
 CHECK(required==(4U|(samples?1U:0U)|(frames?2U:0U)));
 CHECK(f->prepare_step<f->start_step);if(frames)CHECK(f->rx_step<f->start_step);if(samples)CHECK(f->start_step<f->tx_step);
 s->events=f->wait_events==UINT32_MAX?required:f->wait_events;
 s->irq_tx_count=samples?1U:0U;s->irq_rx_count=frames?1U:0U;s->irq_core_count=1;s->wfi_count=1;
 f->reg[DMA_R_STATUS/4U]=2;f->reg[DMA_R_RECEIVED/4U]=samples;f->reg[DMA_R_CONSUMED/4U]=samples;
 f->reg[DMA_R_CAPTURED/4U]=frames*13U;f->reg[DMA_R_SENT/4U]=frames*13U;
 f->reg[DMA_R_INPUT_BYTES/4U]=samples*2U;f->reg[DMA_R_OUTPUT_BYTES/4U]=frames*13U*24U;
 f->reg[DMA_R_CYCLES_LO/4U]=100;f->reg[DMA_R_DETAIL/4U]=f->wait_result?0x305U:0U;
 f->now+=100;
 return f->wait_result ? -(int)f->wait_result:0;
}
static int finish(void *p,void *y,uint32_t n,mfcc_dma_stats *s)
{fake *f=p;CHECK(y==out && n==s->rx_bytes);s->rx_actual_bytes=n;f->finish_step=++f->step;return f->fail_stage==4U?-5:0;}
static int recover(void *p,mfcc_dma_stats *s)
{fake *f=p;(void)s;f->recover_step=++f->step;++f->recovered;return f->recover_fail?-1:0;}
static void init(fake *f,mfcc_dma_ops *o,uint32_t core)
{
 memset(f,0,sizeof(*f));f->now=UINT64_C(987654321000);f->origin=f->now;f->wait_events=UINT32_MAX;
 f->fail_read=f->fail_write=f->corrupt_offset=UINT32_MAX;
 f->reg[DMA_R_ID/4U]=0x4d464343;f->reg[DMA_R_ABI/4U]=0x20000;f->reg[DMA_R_CORE/4U]=core;
 f->reg[DMA_R_FORMAT/4U]=core==1U?0x11828U:0x30020U;f->reg[DMA_R_TAG/4U]=core==1U?0x283fff8aU:0xc556a8e8U;
 f->reg[DMA_R_FRAME_LENGTH/4U]=512;f->reg[DMA_R_HOP/4U]=160;f->reg[DMA_R_NCOEF/4U]=13;f->reg[DMA_R_MAX/4U]=262144;
 o->context=f;o->ticks_per_second=333333343;o->ticks=fticks;o->read=fr;o->write=fw;o->prepare=prep;
 o->receive=rx;o->transmit=tx;o->wait=wait_callback;o->finish=finish;o->recover=recover;
}
static int run(fake *f,mfcc_dma_ops *o,uint32_t core,uint32_t samples,mfcc_dma_stats *s)
{(void)f;return mfcc_dma_run(o,core,pcm,samples,out,MFCC_DMA_MAX_RECORDS,10000,s);}
int main(void)
{
 fake f;mfcc_dma_ops o;mfcc_dma_stats s;uint32_t core,n,i;
 const uint32_t offsets[]={DMA_R_STATUS,DMA_R_RECEIVED,DMA_R_CONSUMED,DMA_R_CAPTURED,DMA_R_SENT,DMA_R_INPUT_BYTES,DMA_R_OUTPUT_BYTES};
 const uint32_t ident[]={DMA_R_ID,DMA_R_ABI,DMA_R_CORE,DMA_R_FORMAT,DMA_R_TAG,DMA_R_FRAME_LENGTH,DMA_R_HOP,DMA_R_NCOEF,DMA_R_MAX};
 for(core=1;core<=2;++core) {
  for(n=0;n<=MFCC_DMA_MAX_SAMPLES;++n) {
   init(&f,&o,core);CHECK(run(&f,&o,core,n,&s)==0);CHECK(s.frames==mfcc_dma_frames(n));
   CHECK(s.elapsed_ticks==101U && s.recovery_attempted==0U && s.failed_offset==UINT32_MAX);
   CHECK(f.absolute_deadline==f.origin+UINT64_C(3333333430));CHECK(f.recovered==0U && f.aborted==1U);
   CHECK((n==0U)==(f.tx_step==0U));CHECK((n<512U)==(f.rx_step==0U));
  }
  for(i=0;i<sizeof(ident)/sizeof(ident[0]);++i) {
   init(&f,&o,core);f.reg[ident[i]/4U]^=1U;CHECK(run(&f,&o,core,85920,&s)==MFCC_DMA_IDENTITY);CHECK(f.writes==0U);
   init(&f,&o,core);f.fail_read=ident[i];CHECK(run(&f,&o,core,85920,&s)==MFCC_DMA_IO);CHECK(f.writes==0U);
  }
  for(i=0;i<sizeof(offsets)/sizeof(offsets[0]);++i) {
   init(&f,&o,core);f.corrupt_offset=offsets[i];f.corrupt_value=UINT32_MAX;
   CHECK(run(&f,&o,core,85920,&s)<0);CHECK(s.recovery_attempted==1U && f.recovered==1U && f.abort_step>f.recover_step);
   init(&f,&o,core);f.fail_read=offsets[i];f.wait_result=4U;
   CHECK(run(&f,&o,core,85920,&s)==MFCC_DMA_HARDWARE);CHECK(s.core_error_detail==0x305U && f.detail_before_failed_read>0U);
   CHECK(f.abort_step>f.recover_step && f.reg[DMA_R_DETAIL/4U]==0U);
  }
  for(i=1;i<=4;++i) {
   init(&f,&o,core);f.fail_stage=i;CHECK(run(&f,&o,core,85920,&s)<0);
   CHECK(f.recovered==1U && f.abort_step>f.recover_step && s.recovery_failed==0U);
  }
  for(i=0;i<32;++i)if(i!=7U) {
   init(&f,&o,core);f.wait_events=i;CHECK(run(&f,&o,core,85920,&s)<0);CHECK(s.recovery_attempted==1U);
  }
  init(&f,&o,core);f.wait_result=6;f.recover_fail=1;CHECK(run(&f,&o,core,85920,&s)==MFCC_DMA_TIMEOUT);
  CHECK(s.core_error_detail==0x305U && s.recovery_failed==1U && f.abort_step>f.recover_step);
  init(&f,&o,core);f.now=UINT64_MAX-50U;f.origin=f.now;CHECK(run(&f,&o,core,85920,&s)==0);
  CHECK(s.elapsed_ticks==101U && f.absolute_deadline==f.origin+UINT64_C(3333333430));
  init(&f,&o,core);CHECK(mfcc_dma_run(&o,core,pcm+1,85920,out,MFCC_DMA_MAX_RECORDS,10,&s)==MFCC_DMA_ARGUMENT);
  CHECK(mfcc_dma_run(&o,core,pcm,262145,out,MFCC_DMA_MAX_RECORDS,10,&s)==MFCC_DMA_ARGUMENT);
  CHECK(mfcc_dma_run(&o,core,pcm,85920,out,6941,10,&s)==MFCC_DMA_ARGUMENT);
  CHECK(mfcc_dma_run(&o,core,pcm,85920,out,6942,60001,&s)==MFCC_DMA_ARGUMENT);
 }
 printf("PASS ARM_DMA_HOST checks=%u\n",checks);return 0;
}
