#include "mfcc_dma.h"
#include <string.h>

uint32_t mfcc_dma_frames(uint32_t n) { return n<512U ? 0U : 1U+(n-512U)/160U; }
static int rd(const mfcc_dma_ops *o,uint32_t off,uint32_t *v,mfcc_dma_stats *s)
{ if (o->read(o->context,off,v)==0) return 0; s->failed_offset=off; return MFCC_DMA_IO; }
static int wr(const mfcc_dma_ops *o,uint32_t off,uint32_t v,mfcc_dma_stats *s)
{ if (o->write(o->context,off,v)==0) return 0; s->failed_offset=off; return MFCC_DMA_IO; }
int mfcc_dma_probe(const mfcc_dma_ops *o,uint32_t core,mfcc_dma_stats *s)
{
 const uint32_t expected[][2]={{DMA_R_ID,UINT32_C(0x4d464343)},{DMA_R_ABI,UINT32_C(0x00020000)},
 {DMA_R_CORE,core},{DMA_R_FORMAT,core==1U ? UINT32_C(0x00011828):UINT32_C(0x00030020)},
 {DMA_R_TAG,core==1U ? UINT32_C(0x283fff8a):UINT32_C(0xc556a8e8)},
 {DMA_R_FRAME_LENGTH,512U},{DMA_R_HOP,160U},{DMA_R_NCOEF,13U},{DMA_R_MAX,MFCC_DMA_MAX_SAMPLES}};
 size_t i;
 if (o==NULL || o->read==NULL || s==NULL || (core!=1U && core!=2U)) return MFCC_DMA_ARGUMENT;
 for (i=0;i<sizeof(expected)/sizeof(expected[0]);++i) {
  uint32_t v=0; if (rd(o,expected[i][0],&v,s)!=0) return MFCC_DMA_IO;
  if (v!=expected[i][1]) {s->failed_offset=expected[i][0];return MFCC_DMA_IDENTITY;}
 }
 return 0;
}
static int capture(const mfcc_dma_ops *o,mfcc_dma_stats *s)
{
 uint32_t attempt;
 /* First native fault/code must be saved even if subsequent reads fail. */
 if (rd(o,DMA_R_DETAIL,&s->core_error_detail,s)!=0 || rd(o,DMA_R_STATUS,&s->core_status,s)!=0
  || rd(o,DMA_R_ERRORS,&s->core_error_flags,s)!=0 || rd(o,DMA_R_RECEIVED,&s->input_received,s)!=0
  || rd(o,DMA_R_CONSUMED,&s->input_consumed,s)!=0 || rd(o,DMA_R_CAPTURED,&s->output_captured,s)!=0
  || rd(o,DMA_R_SENT,&s->output_sent,s)!=0 || rd(o,DMA_R_INPUT_BYTES,&s->input_bytes,s)!=0
  || rd(o,DMA_R_OUTPUT_BYTES,&s->output_bytes,s)!=0) return MFCC_DMA_IO;
 for (attempt=0;attempt<8U;++attempt) {
  uint32_t h1=0,l=0,h2=0;
  if (rd(o,DMA_R_CYCLES_HI,&h1,s)!=0 || rd(o,DMA_R_CYCLES_LO,&l,s)!=0 || rd(o,DMA_R_CYCLES_HI,&h2,s)!=0) return MFCC_DMA_IO;
  if (h1==h2) {s->hardware_busy_cycles=((uint64_t)h1<<32)|l;return 0;}
 }
 return MFCC_DMA_TIMEOUT;
}
static int fail(const mfcc_dma_ops *o,mfcc_dma_stats *s,uint64_t start,int result)
{
 (void)capture(o,s);
 s->recovery_attempted=1;
 /* Quiesce/reset DMA before ABORT releases any held AXIS producer payload. */
 if (wr(o,DMA_R_IRQ_ENABLE,0,s)!=0) s->recovery_failed=1;
 if (o->recover(o->context,s)!=0) s->recovery_failed=1;
 if (wr(o,DMA_R_CONTROL,2,s)!=0) s->recovery_failed=1;
 s->elapsed_ticks=o->ticks(o->context)-start;
 return result;
}
int mfcc_dma_run(const mfcc_dma_ops *o,uint32_t core,const int16_t *pcm,uint32_t samples,
                 mfcc_dma_record *out,uint32_t capacity,uint32_t timeout_ms,mfcc_dma_stats *s)
{
 uint32_t frames,required=MFCC_DMA_EVENT_CORE;
 uint64_t start,duration;
 int result;
 if (o==NULL || s==NULL || o->ticks==NULL || o->read==NULL || o->write==NULL || o->prepare==NULL
  || o->receive==NULL || o->transmit==NULL || o->wait==NULL || o->finish==NULL || o->recover==NULL
  || o->ticks_per_second==0U || o->ticks_per_second>UINT64_C(1000000000)
  || samples>MFCC_DMA_MAX_SAMPLES || timeout_ms==0U || timeout_ms>60000U
  || (samples!=0U && (pcm==NULL || ((uintptr_t)pcm&63U)!=0U))) return MFCC_DMA_ARGUMENT;
 frames=mfcc_dma_frames(samples);
 if (frames!=0U && (out==NULL || ((uintptr_t)out&63U)!=0U || capacity<frames*13U)) return MFCC_DMA_ARGUMENT;
 memset(s,0,sizeof(*s));s->failed_offset=UINT32_MAX;s->samples=samples;s->frames=frames;
 s->tx_bytes=samples*2U;s->rx_bytes=frames*13U*24U;
 result=mfcc_dma_probe(o,core,s); if (result!=0) return result;
 /* Identity probing is outside. All per-clip cache/configuration is inside. */
 start=o->ticks(o->context);duration=(o->ticks_per_second*(uint64_t)timeout_ms+999U)/1000U;
 if (wr(o,DMA_R_IRQ_ENABLE,0,s)!=0 || wr(o,DMA_R_CONTROL,2,s)!=0
  || wr(o,DMA_R_IRQ_STATUS,3,s)!=0 || wr(o,DMA_R_SAMPLES,samples,s)!=0)
  return fail(o,s,start,MFCC_DMA_IO);
 if (o->prepare(o->context,s,pcm,s->tx_bytes,out,s->rx_bytes,start+duration)!=0)
  return fail(o,s,start,MFCC_DMA_IO);
 if (wr(o,DMA_R_IRQ_ENABLE,3,s)!=0) return fail(o,s,start,MFCC_DMA_IO);
 if (s->rx_bytes!=0U) {
  required|=MFCC_DMA_EVENT_RX;
  if (o->receive(o->context,out,s->rx_bytes)!=0) return fail(o,s,start,MFCC_DMA_IO);
 }
 if (wr(o,DMA_R_CONTROL,1,s)!=0) return fail(o,s,start,MFCC_DMA_IO);
 if (s->tx_bytes!=0U) {
  required|=MFCC_DMA_EVENT_TX;
  if (o->transmit(o->context,pcm,s->tx_bytes)!=0) return fail(o,s,start,MFCC_DMA_IO);
 }
 result=o->wait(o->context,required,s);
 if (result!=0) return fail(o,s,start,result);
 if ((s->events&required)!=required || (s->events&(MFCC_DMA_EVENT_ERROR|MFCC_DMA_EVENT_DEADLINE))!=0U)
  return fail(o,s,start,MFCC_DMA_SEQUENCE);
 result=capture(o,s);if (result!=0) return fail(o,s,start,result);
 if (s->core_error_flags!=0U || s->core_error_detail!=0U || s->core_status!=2U)
  return fail(o,s,start,MFCC_DMA_HARDWARE);
 if (s->input_received!=samples || s->input_consumed!=samples || s->output_captured!=frames*13U
  || s->output_sent!=frames*13U || s->input_bytes!=s->tx_bytes || s->output_bytes!=s->rx_bytes)
  return fail(o,s,start,MFCC_DMA_SEQUENCE);
 result=o->finish(o->context,out,s->rx_bytes,s);
 if (result!=0) return fail(o,s,start,result);
 s->elapsed_ticks=o->ticks(o->context)-start;
 if (s->elapsed_ticks>=duration) return fail(o,s,start,MFCC_DMA_TIMEOUT);
 return 0;
}
