#ifndef MFCC_DMA_H
#define MFCC_DMA_H
#include <stddef.h>
#include <stdint.h>

#define MFCC_DMA_BASE UINT32_C(0x43c00000)
#define MFCC_DMA_ENGINE_BASE UINT32_C(0x40400000)
#define MFCC_DMA_MAX_SAMPLES UINT32_C(262144)
#define MFCC_DMA_MAX_RECORDS UINT32_C(21268)
#define MFCC_DMA_EVENT_TX UINT32_C(1)
#define MFCC_DMA_EVENT_RX UINT32_C(2)
#define MFCC_DMA_EVENT_CORE UINT32_C(4)
#define MFCC_DMA_EVENT_ERROR UINT32_C(8)
#define MFCC_DMA_EVENT_DEADLINE UINT32_C(16)
enum {
 DMA_R_ID=0x00,DMA_R_ABI=0x04,DMA_R_CONTROL=0x08,DMA_R_STATUS=0x0c,DMA_R_SAMPLES=0x10,
 DMA_R_ERRORS=0x2c,DMA_R_RECEIVED=0x30,DMA_R_CONSUMED=0x34,DMA_R_CAPTURED=0x38,DMA_R_SENT=0x3c,
 DMA_R_CORE=0x40,DMA_R_FORMAT=0x44,DMA_R_FRAME_LENGTH=0x48,DMA_R_HOP=0x4c,DMA_R_NCOEF=0x50,
 DMA_R_MAX=0x54,DMA_R_CYCLES_LO=0x58,DMA_R_CYCLES_HI=0x5c,DMA_R_TAG=0x60,DMA_R_DETAIL=0x64,
 DMA_R_INPUT_BYTES=0x68,DMA_R_OUTPUT_BYTES=0x6c,DMA_R_IRQ_STATUS=0x70,DMA_R_IRQ_ENABLE=0x74
};
enum { MFCC_DMA_OK=0,MFCC_DMA_ARGUMENT=-1,MFCC_DMA_IDENTITY=-2,MFCC_DMA_IO=-3,
       MFCC_DMA_HARDWARE=-4,MFCC_DMA_SEQUENCE=-5,MFCC_DMA_TIMEOUT=-6 };
typedef struct { uint64_t payload; uint32_t frame,index; int32_t bfp; uint32_t flags; } mfcc_dma_record;
typedef struct {
 uint64_t elapsed_ticks,hardware_busy_cycles,wfi_ticks;
 uint32_t samples,frames,tx_bytes,rx_bytes,core_status,core_error_flags,core_error_detail;
 uint32_t input_received,input_consumed,output_captured,output_sent,input_bytes,output_bytes;
 uint32_t tx_dma_status,rx_dma_status,rx_actual_bytes;
 uint32_t irq_tx_count,irq_rx_count,irq_core_count,irq_timer_count;
 uint32_t irq_tx_status,irq_rx_status,irq_core_status,events,wfi_count,deadline_fired;
 uint32_t recovery_attempted,recovery_failed,failed_offset,reserved0,reserved1,reserved2;
} mfcc_dma_stats;
_Static_assert(sizeof(mfcc_dma_record)==24,"DMA record ABI");
_Static_assert(sizeof(mfcc_dma_stats)==152,"DMA statistics ABI");

/* Board-independent transaction orchestration. ARM callbacks implement cache
 * ownership, Simple DMA and IRQ-safe WFI. Host callbacks test ordering/faults. */
typedef struct {
 void *context;
 uint64_t ticks_per_second;
 uint64_t (*ticks)(void *);
 int (*read)(void *,uint32_t,uint32_t *);
 int (*write)(void *,uint32_t,uint32_t);
 int (*prepare)(void *,mfcc_dma_stats *,const void *,uint32_t,void *,uint32_t,uint64_t);
 int (*receive)(void *,void *,uint32_t);
 int (*transmit)(void *,const void *,uint32_t);
 int (*wait)(void *,uint32_t,mfcc_dma_stats *);
 int (*finish)(void *,void *,uint32_t,mfcc_dma_stats *);
 int (*recover)(void *,mfcc_dma_stats *);
} mfcc_dma_ops;
uint32_t mfcc_dma_frames(uint32_t samples);
int mfcc_dma_probe(const mfcc_dma_ops *,uint32_t core,mfcc_dma_stats *);
int mfcc_dma_run(const mfcc_dma_ops *,uint32_t core,const int16_t *,uint32_t,
                 mfcc_dma_record *,uint32_t,uint32_t timeout_ms,mfcc_dma_stats *);
#endif
