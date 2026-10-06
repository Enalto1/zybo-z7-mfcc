#ifndef MFCC_FFT32_H
#define MFCC_FFT32_H

#include <float.h>

/* The arithmetic contract excludes excess-precision float evaluation. */
_Static_assert(sizeof(float) == 4, "FFT requires a 32-bit float object");
_Static_assert(FLT_RADIX == 2, "FFT requires binary floating point");
_Static_assert(FLT_MANT_DIG == 24, "FFT requires binary32 precision");
_Static_assert(FLT_MIN_EXP == -125 && FLT_MAX_EXP == 128,
               "FFT requires binary32 exponent range");
_Static_assert(FLT_EVAL_METHOD == 0, "FFT requires float evaluation without promotion");

enum { FFT32_LENGTH = 512 };

/*
 * Original scalar radix-2 decimation-in-time implementation, from the DFT
 * equations. The transform is forward (negative exponent), unscaled, and
 * returns all 512 bins in natural order. Work arrays are caller-owned, distinct,
 * nonoverlapping arrays of at least FFT32_LENGTH floats. There is no allocation.
 *
 * Return 0 on success or -1 for NULL/same-array arguments or nonfinite values.
 * On arithmetic failure the work arrays may be partially transformed.
 * Build with FMA contraction and unsafe reassociation disabled; the compiler
 * flags, generated twiddles, and C library version belong in the run manifest.
 */
int fft32_forward(float real[FFT32_LENGTH], float imag[FFT32_LENGTH]);

#endif
