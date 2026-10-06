#ifndef ARM_MFCC_SUPPORT_H
#define ARM_MFCC_SUPPORT_H

#include <stddef.h>
#include <stdint.h>
#include "arm_protocol.h"

int arm_startup(const char *marker);
void arm_publish_status(void);
void arm_complete(uint32_t error);
void arm_idle(void) __attribute__((noreturn));
void arm_flush(const void *address, size_t bytes);
void arm_invalidate(const void *address, size_t bytes);
uint32_t arm_read_fpscr(void);
uint32_t arm_crc32(const void *address, size_t bytes);

#endif
