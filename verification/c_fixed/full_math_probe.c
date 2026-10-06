#define _CRT_SECURE_NO_WARNINGS
#include "mfcc_fixed_full.h"

#include <inttypes.h>
#include <stdio.h>

/* Expected values come from Python unbounded integer arithmetic. This probe
 * deliberately contains no second implementation of the decomposition. */
int main(int argc, char **argv)
{
    FILE *stream;
    char kind;
    uint64_t scale_count = 0, floor_count = 0;
    if (argc != 2) return 2;
    stream = fopen(argv[1], "r");
    if (stream == NULL) return 3;
    while (fscanf(stream, " %c", &kind) == 1) {
        if (kind == 'S') {
            int64_t input, expected;
            int32_t actual = 0;
            if (fscanf(stream, "%" SCNd64 " %" SCNd64, &input, &expected) != 2) return 4;
            if (cf_full_log_scale(input, &actual) != CF_FULL_OK || actual != expected) {
                fprintf(stderr, "scale mismatch at %" PRId64 ": %" PRId32 " != %" PRId64 "\n",
                        input, actual, expected);
                return 5;
            }
            ++scale_count;
        } else if (kind == 'F') {
            uint64_t input;
            int32_t exponent;
            uint32_t actual = 7, expected;
            if (fscanf(stream, "%" SCNu64 " %" SCNd32 " %" SCNu32,
                       &input, &exponent, &expected) != 3) return 6;
            if (cf_full_floor(input, exponent, &actual) != CF_FULL_OK || actual != expected) {
                fprintf(stderr, "floor mismatch: T=%" PRIu64 " e=%" PRId32 "\n", input, exponent);
                return 7;
            }
            ++floor_count;
        } else return 8;
    }
    if (ferror(stream)) return 9;
    if (fclose(stream) != 0) return 10;
    printf("{\"scale_checks\":%" PRIu64 ",\"floor_checks\":%" PRIu64 ",\"mismatches\":0}\n",
           scale_count, floor_count);
    return 0;
}
