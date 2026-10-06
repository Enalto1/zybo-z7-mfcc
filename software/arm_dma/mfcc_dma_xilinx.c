#include "mfcc_dma_xilinx.h"
#include "dma_platform.h"
#include "xil_cache.h"
#include "xil_exception.h"
#include "xil_io.h"
#include "xil_mmu.h"
#include "xtime_l.h"
#include <limits.h>
#include <string.h>

#if !defined(__arm__) || !defined(__ARM_PCS_VFP)
#error "Use the standalone Cortex-A9 hard-float BSP"
#endif
_Static_assert(sizeof(uintptr_t)==4,"A9 pointer width");
_Static_assert(((DEVICE_MEMORY|UINT32_C(0x10))&3U)==2U,"A9 XN section must not set PXN");
_Static_assert(DMA_TX_IRQ==61U && DMA_RX_IRQ==62U && DMA_CORE_IRQ==63U && DMA_TIMER_IRQ==29U,"Verified GIC IDs");

static void barrier(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static uint32_t irq_save(void)
{ uint32_t value;__asm__ volatile("mrs %0,cpsr\n\tcpsid i":"=r"(value)::"memory");return value; }
static void irq_restore(uint32_t value)
{ __asm__ volatile("msr cpsr_c,%0\n\tisb sy"::"r"(value):"memory"); }
static uint64_t ticks(void *unused)
{ XTime t;(void)unused;XTime_GetTime(&t);return (uint64_t)t; }
static uint32_t rounded(uint32_t bytes) { return (bytes+31U)&~UINT32_C(31); }
static int read_core(void *unused,uint32_t offset,uint32_t *value)
{ (void)unused;if (offset>DMA_R_IRQ_ENABLE || (offset&3U)!=0U)return -1;barrier();*value=Xil_In32(MFCC_DMA_BASE+offset);barrier();return 0; }
static int write_core(void *unused,uint32_t offset,uint32_t value)
{ (void)unused;if (offset>DMA_R_IRQ_ENABLE || (offset&3U)!=0U)return -1;barrier();Xil_Out32(MFCC_DMA_BASE+offset,value);barrier();return 0; }
static void clear_pending(mfcc_dma_arm *a,uint32_t id)
{ XScuGic_DistWriteReg(&a->gic,XSCUGIC_PENDING_CLR_OFFSET+(id/32U)*4U,UINT32_C(1)<<(id%32U)); }

/* ISRs only read/ACK their source and publish volatile flags/diagnostics.
 * In particular, DMA reset/recovery is never performed inside an ISR. */
static void tx_irq(void *opaque)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 uint32_t pending=XAxiDma_IntrGetIrq(&a->dma,XAXIDMA_DMA_TO_DEVICE);
 XAxiDma_IntrAckIrq(&a->dma,pending,XAXIDMA_DMA_TO_DEVICE);
 if (a->active!=NULL) {
  ++a->active->irq_tx_count;a->active->irq_tx_status|=pending;
  if ((pending&XAXIDMA_IRQ_IOC_MASK)!=0U)a->active->events|=MFCC_DMA_EVENT_TX;
  if ((pending&XAXIDMA_IRQ_ERROR_MASK)!=0U)a->active->events|=MFCC_DMA_EVENT_ERROR;
 } else ++a->unowned_irqs;
 barrier();
}
static void rx_irq(void *opaque)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 uint32_t pending=XAxiDma_IntrGetIrq(&a->dma,XAXIDMA_DEVICE_TO_DMA);
 XAxiDma_IntrAckIrq(&a->dma,pending,XAXIDMA_DEVICE_TO_DMA);
 if (a->active!=NULL) {
  ++a->active->irq_rx_count;a->active->irq_rx_status|=pending;
  if ((pending&XAXIDMA_IRQ_IOC_MASK)!=0U)a->active->events|=MFCC_DMA_EVENT_RX;
  if ((pending&XAXIDMA_IRQ_ERROR_MASK)!=0U)a->active->events|=MFCC_DMA_EVENT_ERROR;
 } else ++a->unowned_irqs;
 barrier();
}
static void core_irq(void *opaque)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 uint32_t pending=Xil_In32(MFCC_DMA_BASE+DMA_R_IRQ_STATUS);
 Xil_Out32(MFCC_DMA_BASE+DMA_R_IRQ_STATUS,pending&3U);
 if (a->active!=NULL) {
  ++a->active->irq_core_count;a->active->irq_core_status|=pending;
  if ((pending&1U)!=0U)a->active->events|=MFCC_DMA_EVENT_CORE;
  if ((pending&~UINT32_C(1))!=0U)a->active->events|=MFCC_DMA_EVENT_ERROR;
 } else ++a->unowned_irqs;
 barrier();
}
static void timer_irq(void *opaque)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 XScuTimer_ClearInterruptStatus(&a->timer);XScuTimer_Stop(&a->timer);XScuTimer_DisableInterrupt(&a->timer);
 if (a->active!=NULL) {
  ++a->active->irq_timer_count;a->active->deadline_fired=1;
  a->active->events|=MFCC_DMA_EVENT_DEADLINE;
 } else ++a->unowned_irqs;
 barrier();
}
static void stop_sources(mfcc_dma_arm *a)
{
 XScuTimer_Stop(&a->timer);XScuTimer_DisableInterrupt(&a->timer);XScuTimer_ClearInterruptStatus(&a->timer);
 XAxiDma_IntrDisable(&a->dma,(uint32_t)XAXIDMA_IRQ_ALL_MASK,XAXIDMA_DMA_TO_DEVICE);
 XAxiDma_IntrDisable(&a->dma,(uint32_t)XAXIDMA_IRQ_ALL_MASK,XAXIDMA_DEVICE_TO_DMA);
 XAxiDma_IntrAckIrq(&a->dma,XAXIDMA_IRQ_ALL_MASK,XAXIDMA_DMA_TO_DEVICE);
 XAxiDma_IntrAckIrq(&a->dma,XAXIDMA_IRQ_ALL_MASK,XAXIDMA_DEVICE_TO_DMA);
 Xil_Out32(MFCC_DMA_BASE+DMA_R_IRQ_ENABLE,0);Xil_Out32(MFCC_DMA_BASE+DMA_R_IRQ_STATUS,3);
 clear_pending(a,DMA_TX_IRQ);clear_pending(a,DMA_RX_IRQ);clear_pending(a,DMA_CORE_IRQ);clear_pending(a,DMA_TIMER_IRQ);
 barrier();
}
static int prepare(void *opaque,mfcc_dma_stats *s,const void *pcm,uint32_t tx_bytes,void *out,uint32_t rx_bytes,uint64_t deadline)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 uint32_t saved=irq_save();
 uint64_t now,remaining,load;
 stop_sources(a);a->active=s;a->deadline=deadline;
 if (tx_bytes!=0U)Xil_DCacheFlushRange((INTPTR)pcm,rounded(tx_bytes));
 if (rx_bytes!=0U) {
  Xil_DCacheFlushRange((INTPTR)out,rounded(rx_bytes));
  Xil_DCacheInvalidateRange((INTPTR)out,rounded(rx_bytes));
 }
 barrier();
 now=ticks(NULL);remaining=deadline-now;
 if (remaining==0U || remaining>(uint64_t)INT64_MAX) {irq_restore(saved);return -1;}
 /* Private timer and global timer both use CPU/2. Prescaler255 safely fits
  * the supported 60-second upper bound in a 32-bit one-shot timer. */
 load=(remaining+255U)/256U;
 if (load==0U || load>UINT32_MAX) {irq_restore(saved);return -1;}
 XScuTimer_LoadTimer(&a->timer,(uint32_t)load);XScuTimer_EnableInterrupt(&a->timer);XScuTimer_Start(&a->timer);
 XAxiDma_IntrEnable(&a->dma,(XAXIDMA_IRQ_IOC_MASK|XAXIDMA_IRQ_ERROR_MASK),XAXIDMA_DMA_TO_DEVICE);
 XAxiDma_IntrEnable(&a->dma,(XAXIDMA_IRQ_IOC_MASK|XAXIDMA_IRQ_ERROR_MASK),XAXIDMA_DEVICE_TO_DMA);
 barrier();irq_restore(saved);return 0;
}
static int receive(void *opaque,void *out,uint32_t bytes)
{ mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;return XAxiDma_SimpleTransfer(&a->dma,(UINTPTR)out,bytes,XAXIDMA_DEVICE_TO_DMA)==XST_SUCCESS ? 0:-1; }
static int transmit(void *opaque,const void *pcm,uint32_t bytes)
{ mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;return XAxiDma_SimpleTransfer(&a->dma,(UINTPTR)pcm,bytes,XAXIDMA_DMA_TO_DEVICE)==XST_SUCCESS ? 0:-1; }
static int wait_irqs(void *opaque,uint32_t required,mfcc_dma_stats *stats)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 volatile mfcc_dma_stats *s=stats;
 for (;;) {
  uint32_t saved=irq_save(),events=s->events;
  uint64_t before=ticks(NULL);
  if ((events&MFCC_DMA_EVENT_ERROR)!=0U) {irq_restore(saved);return MFCC_DMA_HARDWARE;}
  if ((events&MFCC_DMA_EVENT_DEADLINE)!=0U || (int64_t)(before-a->deadline)>=0) {
   s->deadline_fired=1;irq_restore(saved);return MFCC_DMA_TIMEOUT;
  }
  if ((events&required)==required) {irq_restore(saved);return 0;}
  if ((saved&UINT32_C(0x80))!=0U || s->wfi_count>=1000000U) {irq_restore(saved);return MFCC_DMA_SEQUENCE;}
  ++s->wfi_count;
  /* A9 TRM 2.4.2: pending IRQ wakes WFI regardless of CPSR.I. Keeping I
   * masked across check+WFI closes the completion-before-sleep race. */
  __asm__ volatile("dsb sy\n\twfi\n\tisb sy" ::: "memory");
  s->wfi_ticks+=ticks(NULL)-before;
  irq_restore(saved); /* Deliver pending ISR(s), then recheck volatile flags. */
 }
}
static void dma_snapshot(mfcc_dma_arm *a,mfcc_dma_stats *s)
{
 s->tx_dma_status=XAxiDma_ReadReg(a->dma.RegBase,XAXIDMA_TX_OFFSET+XAXIDMA_SR_OFFSET);
 s->rx_dma_status=XAxiDma_ReadReg(a->dma.RegBase,XAXIDMA_RX_OFFSET+XAXIDMA_SR_OFFSET);
 s->rx_actual_bytes=s->rx_bytes==0U ? 0U : XAxiDma_ReadReg(a->dma.RegBase,XAXIDMA_RX_OFFSET+XAXIDMA_BUFFLEN_OFFSET);
 s->reserved0=a->unowned_irqs;
}
static int finish(void *opaque,void *out,uint32_t bytes,mfcc_dma_stats *s)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 uint32_t saved=irq_save();
 dma_snapshot(a,s);stop_sources(a);a->active=NULL;irq_restore(saved);
 if (((s->tx_dma_status|s->rx_dma_status)&XAXIDMA_ERR_ALL_MASK)!=0U) return MFCC_DMA_HARDWARE;
 if ((s->tx_bytes!=0U && (s->tx_dma_status&XAXIDMA_IDLE_MASK)==0U)
  || (s->rx_bytes!=0U && (s->rx_dma_status&XAXIDMA_IDLE_MASK)==0U)
  || s->rx_actual_bytes!=bytes || s->reserved0!=0U) return MFCC_DMA_SEQUENCE;
 if (bytes!=0U)Xil_DCacheInvalidateRange((INTPTR)out,rounded(bytes));
 barrier();return 0;
}
static int recover(void *opaque,mfcc_dma_stats *s)
{
 mfcc_dma_arm *a=(mfcc_dma_arm *)opaque;
 uint32_t saved=irq_save(),attempt;
 uint64_t start=ticks(NULL);
 dma_snapshot(a,s);stop_sources(a);a->active=NULL;
 XAxiDma_Reset(&a->dma);irq_restore(saved);
 for (attempt=0;attempt<1000000U;++attempt) {
  if (XAxiDma_ResetIsDone(&a->dma))return 0;
  if (ticks(NULL)-start>(uint64_t)COUNTS_PER_SECOND/10U)break;
 }
 return -1;
}
int mfcc_dma_arm_init(mfcc_dma_ops *o,mfcc_dma_arm *a,uint32_t core)
{
 XAxiDma_Config *dc=XAxiDma_LookupConfig(DMA_DEVICE_ID);
 XScuGic_Config *gc=XScuGic_LookupConfig(DMA_GIC_DEVICE_ID);
 XScuTimer_Config *tc=XScuTimer_LookupConfig(DMA_TIMER_DEVICE_ID);
 const uint32_t ids[]={DMA_TX_IRQ,DMA_RX_IRQ,DMA_CORE_IRQ,DMA_TIMER_IRQ};
 Xil_InterruptHandler handlers[]={tx_irq,rx_irq,core_irq,timer_irq};
 size_t i;mfcc_dma_stats probe_stats;
 if (dc==NULL || gc==NULL || tc==NULL || dc->BaseAddr!=MFCC_DMA_ENGINE_BASE || dc->HasSg!=0
  || dc->HasMm2S==0 || dc->HasS2Mm==0 || dc->HasMm2SDRE==0 || dc->HasS2MmDRE==0
  || dc->SgLengthWidth!=23 || dc->MicroDmaMode!=0 || dc->Mm2SDataWidth!=64 || dc->S2MmDataWidth!=64) return -1;
 memset(a,0,sizeof(*a));
 Xil_SetTlbAttributes(MFCC_DMA_BASE,DEVICE_MEMORY|UINT32_C(0x10));
 Xil_SetTlbAttributes(MFCC_DMA_ENGINE_BASE,DEVICE_MEMORY|UINT32_C(0x10));barrier();
 /* Refuse a legacy/wrong core before any control or DMA side effects. */
 memset(o,0,sizeof(*o));memset(&probe_stats,0,sizeof(probe_stats));o->context=a;o->read=read_core;
 if(mfcc_dma_probe(o,core,&probe_stats)!=0)return -1;
 if (XAxiDma_CfgInitialize(&a->dma,dc)!=XST_SUCCESS || XAxiDma_HasSg(&a->dma)
  || XScuGic_CfgInitialize(&a->gic,gc,gc->CpuBaseAddress)!=XST_SUCCESS
  || XScuTimer_CfgInitialize(&a->timer,tc,tc->BaseAddr)!=XST_SUCCESS) return -1;
 XScuTimer_DisableAutoReload(&a->timer);XScuTimer_SetPrescaler(&a->timer,255U);stop_sources(a);
 for (i=0;i<sizeof(ids)/sizeof(ids[0]);++i) {
  XScuGic_SetPriorityTriggerType(&a->gic,ids[i],0xa0U,1U); /* active-high level */
  if (ids[i]>=32U)XScuGic_InterruptMaptoCpu(&a->gic,0U,ids[i]);
  if (XScuGic_Connect(&a->gic,ids[i],handlers[i],a)!=XST_SUCCESS)return -1;
  XScuGic_Enable(&a->gic,ids[i]);
 }
 Xil_ExceptionInit();Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_INT,(Xil_ExceptionHandler)XScuGic_InterruptHandler,&a->gic);
 Xil_ExceptionEnableMask((uint32_t)XIL_EXCEPTION_IRQ);
 o->context=a;o->ticks_per_second=(uint64_t)COUNTS_PER_SECOND;o->ticks=ticks;o->read=read_core;o->write=write_core;
 o->prepare=prepare;o->receive=receive;o->transmit=transmit;o->wait=wait_irqs;o->finish=finish;o->recover=recover;
 return 0;
}
