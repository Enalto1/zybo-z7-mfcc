#include "fft32.h"
#include "mfcc_tables.h"

#include <math.h>
#include <stddef.h>

int fft32_forward(float real[FFT32_LENGTH], float imag[FFT32_LENGTH])
{
    unsigned i;
    unsigned reversed = 0;
    unsigned length;

    if (real == NULL || imag == NULL || real == imag) {
        return -1;
    }
    for (i = 0; i < FFT32_LENGTH; ++i) {
        if (!isfinite(real[i]) || !isfinite(imag[i])) {
            return -1;
        }
    }

    /* Bit-reverse the inputs so iterative DIT butterflies produce natural bins. */
    for (i = 1; i < FFT32_LENGTH; ++i) {
        unsigned bit = FFT32_LENGTH / 2;
        while ((reversed & bit) != 0u) {
            reversed ^= bit;
            bit >>= 1;
        }
        reversed ^= bit;
        if (i < reversed) {
            float temporary = real[i];
            real[i] = real[reversed];
            real[reversed] = temporary;
            temporary = imag[i];
            imag[i] = imag[reversed];
            imag[reversed] = temporary;
        }
    }

    for (length = 2; length <= FFT32_LENGTH; length <<= 1) {
        const unsigned half = length / 2;
        const unsigned twiddle_stride = FFT32_LENGTH / length;
        unsigned first;
        for (first = 0; first < FFT32_LENGTH; first += length) {
            unsigned offset;
            for (offset = 0; offset < half; ++offset) {
                const unsigned even = first + offset;
                const unsigned odd = even + half;
                const unsigned twiddle = offset * twiddle_stride;
                const float wr = mfcc_twiddle_re[twiddle];
                const float wi = mfcc_twiddle_im[twiddle];
                const float odd_re = real[odd];
                const float odd_im = imag[odd];
                const float rr = wr * odd_re;
                const float ii = wi * odd_im;
                const float ri = wr * odd_im;
                const float ir = wi * odd_re;
                const float product_re = rr - ii;
                const float product_im = ri + ir;
                const float even_re = real[even];
                const float even_im = imag[even];

                real[even] = even_re + product_re;
                imag[even] = even_im + product_im;
                real[odd] = even_re - product_re;
                imag[odd] = even_im - product_im;
            }
        }
    }

    for (i = 0; i < FFT32_LENGTH; ++i) {
        if (!isfinite(real[i]) || !isfinite(imag[i])) {
            return -1;
        }
    }
    return 0;
}
