#ifndef MFCC_ACCEL_XILINX_H
#define MFCC_ACCEL_XILINX_H
#include "mfcc_accel.h"
typedef struct { uint32_t base_address; } mfcc_accel_arm_context;
void mfcc_accel_arm_init(mfcc_accel_bus *bus, mfcc_accel_arm_context *context);
uint64_t mfcc_accel_arm_ticks_per_second(void);
#endif
