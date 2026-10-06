#ifndef MFCC_FP32_ACCEL_XILINX_H
#define MFCC_FP32_ACCEL_XILINX_H
#include "mfcc_fp32_accel.h"
typedef struct { uint32_t base_address; } mfcc_fp32_accel_arm_context;
void mfcc_fp32_accel_arm_init(mfcc_fp32_accel_bus *bus, mfcc_fp32_accel_arm_context *context);
uint64_t mfcc_fp32_accel_arm_ticks_per_second(void);
#endif
