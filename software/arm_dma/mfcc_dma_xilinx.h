#ifndef MFCC_DMA_XILINX_H
#define MFCC_DMA_XILINX_H
#include "mfcc_dma.h"
#include "xaxidma.h"
#include "xscugic.h"
#include "xscutimer.h"
typedef struct {
 XAxiDma dma;
 XScuGic gic;
 XScuTimer timer;
 volatile mfcc_dma_stats *active;
 uint64_t deadline;
 uint32_t unowned_irqs;
} mfcc_dma_arm;
int mfcc_dma_arm_init(mfcc_dma_ops *,mfcc_dma_arm *,uint32_t core);
#endif
