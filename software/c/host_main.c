/* File-I/O adapter for numerical validation of the streaming MFCC core.
 * It reads each PCM16 sample once; the core owns overlap and clip state.
 * All dumps are explicitly little-endian, independently of host byte order.
 * No ARM/FPGA performance measurement is made by this program.
 */
#define _CRT_SECURE_NO_WARNINGS
#include "mfcc.h"

#include <errno.h>
#include <fenv.h>
#include <float.h>
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_CHUNK_SAMPLES 1048576u
#define DEFAULT_CHUNK_SAMPLES 4096u

_Static_assert(sizeof(float) == sizeof(uint32_t), "Float dumps require 32-bit float storage");

typedef struct {
    const char *name;
    const char *dtype;
    size_t columns;
    size_t item_bytes;
    int matrix;
    FILE *file;
    uint64_t bytes;
} dump_stream;

enum {
    INPUT_FLOAT, PREEMPHASIS, FRAME_STARTS, FRAME_IDS, FRAMES, WINDOWED,
    FFT, POWER, MEL, LOG_MEL, DCT, MFCC, FRAME_ENERGY, STREAM_COUNT
};

typedef struct {
    const char *message;
    unsigned io_errors;
    unsigned nonfinite_detected;
    unsigned contract_errors;
} diagnostics;

static char *path_join(const char *directory, const char *filename)
{
    size_t a = strlen(directory), b = strlen(filename);
    char *result;
    if (a > SIZE_MAX - b - 2u) return NULL;
    result = (char *)malloc(a + b + 2u);
    if (!result) return NULL;
    memcpy(result, directory, a);
    if (a && directory[a - 1u] != '/' && directory[a - 1u] != '\\')
        result[a++] = '/';
    memcpy(result + a, filename, b + 1u);
    return result;
}

static void set_error(diagnostics *d, const char *message)
{
    if (!d->message) d->message = message;
    fprintf(stderr, "MFCC host error: %s\n", message);
}

static int write_bytes(dump_stream *stream, const unsigned char *data, size_t size)
{
    size_t written = fwrite(data, 1u, size, stream->file);
    stream->bytes += (uint64_t)written;
    return written == size ? 0 : -1;
}

static int write_float32(dump_stream *stream, const float *values, size_t count)
{
    unsigned char bytes[MFCC_FFT_FLOATS * 4u];
    size_t i;
    if (count > MFCC_FFT_FLOATS) return -1;
    for (i = 0; i < count; ++i) {
        uint32_t bits;
        memcpy(&bits, values + i, sizeof(bits));
        bytes[4u*i] = (unsigned char)bits;
        bytes[4u*i + 1u] = (unsigned char)(bits >> 8);
        bytes[4u*i + 2u] = (unsigned char)(bits >> 16);
        bytes[4u*i + 3u] = (unsigned char)(bits >> 24);
    }
    return write_bytes(stream, bytes, count * 4u);
}

static int write_uint64(dump_stream *stream, uint64_t value)
{
    unsigned char bytes[8];
    unsigned i;
    for (i = 0; i < 8u; ++i) bytes[i] = (unsigned char)(value >> (8u*i));
    return write_bytes(stream, bytes, sizeof(bytes));
}

static int all_finite(const float *values, size_t count)
{
    size_t i;
    for (i = 0; i < count; ++i) if (!isfinite(values[i])) return 0;
    return 1;
}

static int frame_finite(const mfcc_frame *frame)
{
    return all_finite(frame->frames, MFCC_FRAME_LENGTH)
        && all_finite(frame->windowed, MFCC_FRAME_LENGTH)
        && all_finite(frame->fft, MFCC_FFT_FLOATS)
        && all_finite(frame->power, MFCC_NBINS)
        && all_finite(frame->mel_energies, MFCC_NMEL)
        && all_finite(frame->log_mel, MFCC_NMEL)
        && all_finite(frame->dct, MFCC_NCEPS)
        && all_finite(frame->mfcc, MFCC_NCEPS)
        && isfinite(frame->frame_energy);
}

static int dump_frame(dump_stream *streams, const mfcc_frame *frame)
{
    return write_uint64(&streams[FRAME_STARTS], frame->start_sample)
        || write_uint64(&streams[FRAME_IDS], frame->frame_id)
        || write_float32(&streams[FRAMES], frame->frames, MFCC_FRAME_LENGTH)
        || write_float32(&streams[WINDOWED], frame->windowed, MFCC_FRAME_LENGTH)
        || write_float32(&streams[FFT], frame->fft, MFCC_FFT_FLOATS)
        || write_float32(&streams[POWER], frame->power, MFCC_NBINS)
        || write_float32(&streams[MEL], frame->mel_energies, MFCC_NMEL)
        || write_float32(&streams[LOG_MEL], frame->log_mel, MFCC_NMEL)
        || write_float32(&streams[DCT], frame->dct, MFCC_NCEPS)
        || write_float32(&streams[MFCC], frame->mfcc, MFCC_NCEPS)
        || write_float32(&streams[FRAME_ENERGY], &frame->frame_energy, 1u);
}

static void json_string(FILE *file, const char *value)
{
    const unsigned char *p = (const unsigned char *)value;
    fputc('"', file);
    while (*p) {
        unsigned char c = *p++;
        if (c == '"' || c == '\\') { fputc('\\', file); fputc(c, file); }
        else if (c < 0x20u) fprintf(file, "\\u%04x", (unsigned)c);
        else fputc(c, file);
    }
    fputc('"', file);
}

static int write_manifest(const char *directory, const char *input,
                          const dump_stream *streams, const diagnostics *d,
                          uint64_t samples, uint64_t frames, size_t chunk,
                          int native_little_endian, int runtime_rounding)
{
    char *path = path_join(directory, "host_manifest.json");
    FILE *file;
    unsigned i;
    int failed;
    if (!path) return -1;
    file = fopen(path, "wb");
    free(path);
    if (!file) return -1;
    fprintf(file, "{\n  \"schema_version\": 1,\n  \"profile_id\": \"comparison_raw13\",\n");
    fprintf(file, "  \"status\": \"%s\",\n  \"error\": ", d->message ? "failed" : "passed");
    if (d->message) json_string(file, d->message); else fprintf(file, "null");
    fprintf(file, ",\n  \"input_path\": "); json_string(file, input);
    fprintf(file, ",\n  \"storage\": \"headerless little-endian, C row-major\",\n");
    fprintf(file, "  \"sample_count\": %" PRIu64 ",\n  \"frame_count\": %" PRIu64 ",\n", samples, frames);
    fprintf(file, "  \"chunk_size_samples\": %zu,\n", chunk);
    fprintf(file, "  \"frame_policy\": \"L512 H160 complete frames only; no tail padding\",\n");
    fprintf(file, "  \"float_properties\": {\"sizeof_float\": %zu, \"radix\": %d, \"mantissa_bits\": %d, \"max_exponent\": %d, \"eval_method\": %d, \"native_little_endian\": %s, \"output_endianness\": \"little\"},\n",
            sizeof(float), FLT_RADIX, FLT_MANT_DIG, FLT_MAX_EXP, FLT_EVAL_METHOD, native_little_endian ? "true" : "false");
    fprintf(file, "  \"runtime_rounding\": {\"checked_before_input\": true, \"fegetround_value\": %d, \"FE_TONEAREST_value\": %d, \"is_FE_TONEAREST\": %s},\n",
            runtime_rounding, FE_TONEAREST, runtime_rounding == FE_TONEAREST ? "true" : "false");
    fprintf(file, "  \"working_memory_bytes\": {\"mfcc_state\": %zu, \"mfcc_frame\": %zu, \"input_chunk\": %zu},\n", sizeof(mfcc_state), sizeof(mfcc_frame), chunk*2u);
    fprintf(file, "  \"timing\": {\"measured\": false, \"reason\": \"Numerical validation with file I/O and stage dumps; no performance benchmark\"},\n");
    fprintf(file, "  \"error_counts\": {\"io\": %u, \"nonfinite_detected\": %u, \"input_or_core_contract\": %u},\n", d->io_errors, d->nonfinite_detected, d->contract_errors);
    fprintf(file, "  \"arrays\": {\n");
    for (i = 0; i < STREAM_COUNT; ++i) {
        const dump_stream *s = &streams[i];
        uint64_t rows = s->bytes / (s->item_bytes*s->columns);
        fprintf(file, "    \"%s\": {\"file\": \"%s.bin\", \"dtype\": \"%s\", \"shape\": [%" PRIu64, s->name, s->name, s->dtype, rows);
        if (s->matrix) fprintf(file, ", %zu", s->columns);
        fprintf(file, "], \"bytes\": %" PRIu64, s->bytes);
        if (i == FFT) fprintf(file, ", \"complex_layout\": \"interleaved float32 real,imag; bins 0..256\"");
        fprintf(file, "}%s\n", i + 1u < STREAM_COUNT ? "," : "");
    }
    fprintf(file, "  }\n}\n");
    failed = ferror(file);
    if (fclose(file) != 0) failed = 1;
    return failed ? -1 : 0;
}

static void usage(const char *program)
{
    fprintf(stderr, "Usage: %s --input PCM_S16LE --output EXISTING_DIR [--chunk-size N]\n", program);
}

int main(int argc, char **argv)
{
    dump_stream streams[STREAM_COUNT] = {
        {"input_float", "<f4", 1, 4, 0, NULL, 0},
        {"preemphasis", "<f4", 1, 4, 0, NULL, 0},
        {"frame_starts", "<u8", 1, 8, 0, NULL, 0},
        {"frame_ids", "<u8", 1, 8, 0, NULL, 0},
        {"frames", "<f4", MFCC_FRAME_LENGTH, 4, 1, NULL, 0},
        {"windowed", "<f4", MFCC_FRAME_LENGTH, 4, 1, NULL, 0},
        {"fft", "<c8", MFCC_NBINS, 8, 1, NULL, 0},
        {"power", "<f4", MFCC_NBINS, 4, 1, NULL, 0},
        {"mel_energies", "<f4", MFCC_NMEL, 4, 1, NULL, 0},
        {"log_mel", "<f4", MFCC_NMEL, 4, 1, NULL, 0},
        {"dct", "<f4", MFCC_NCEPS, 4, 1, NULL, 0},
        {"mfcc", "<f4", MFCC_NCEPS, 4, 1, NULL, 0},
        {"frame_energy", "<f4", 1, 4, 0, NULL, 0}
    };
    const char *input = NULL, *output = NULL;
    size_t chunk = DEFAULT_CHUNK_SAMPLES;
    diagnostics diag = {NULL, 0, 0, 0};
    FILE *pcm = NULL;
    unsigned char *buffer = NULL;
    mfcc_state *state = NULL;
    mfcc_frame *frame = NULL;
    uint64_t samples = 0, frames = 0;
    const uint32_t endian_probe = 1u;
    float float_probe = 1.0f;
    uint32_t float_bits = 0;
    int native_little = *(const unsigned char *)&endian_probe == 1u;
    int runtime_rounding = fegetround();
    int i;
    for (i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--input") && i+1 < argc && !input) input = argv[++i];
        else if (!strcmp(argv[i], "--output") && i+1 < argc && !output) output = argv[++i];
        else if (!strcmp(argv[i], "--chunk-size") && i+1 < argc) {
            char *end;
            unsigned long long parsed;
            const char *argument = argv[++i];
            errno = 0;
            if (*argument < '0' || *argument > '9') { usage(argv[0]); return 2; }
            parsed = strtoull(argument, &end, 10);
            if (errno || *end || parsed == 0 || parsed > MAX_CHUNK_SAMPLES) { usage(argv[0]); return 2; }
            chunk = (size_t)parsed;
        }
        else { usage(argv[0]); return 2; }
    }
    if (!input || !output || !*output) { usage(argv[0]); return 2; }
    if (runtime_rounding != FE_TONEAREST) {
        diag.contract_errors++;
        set_error(&diag, "Host runtime rounding mode must be FE_TONEAREST before consuming PCM.");
        goto cleanup;
    }
    memcpy(&float_bits, &float_probe, sizeof(float_bits));
    if (CHAR_BIT != 8 || sizeof(float) != 4 || FLT_RADIX != 2 || FLT_MANT_DIG != 24
        || FLT_MAX_EXP != 128 || FLT_EVAL_METHOD != 0 || float_bits != UINT32_C(0x3f800000)) {
        diag.contract_errors++;
        set_error(&diag, "Host does not provide required binary32 float semantics.");
        goto cleanup;
    }
    pcm = fopen(input, "rb");
    if (!pcm) { diag.io_errors++; set_error(&diag, "Could not open PCM input."); goto cleanup; }
    for (i = 0; i < STREAM_COUNT; ++i) {
        char filename[64];
        char *path;
        (void)snprintf(filename, sizeof(filename), "%s.bin", streams[i].name);
        path = path_join(output, filename);
        if (path) { streams[i].file = fopen(path, "wb"); free(path); }
        if (!streams[i].file) { diag.io_errors++; set_error(&diag, "Could not open stage output; output directory must exist."); goto cleanup; }
    }
    buffer = (unsigned char *)malloc(chunk*2u);
    state = (mfcc_state *)malloc(sizeof(*state));
    frame = (mfcc_frame *)malloc(sizeof(*frame));
    if (!buffer || !state || !frame) { set_error(&diag, "Allocation failed."); goto cleanup; }
    if (mfcc_init(state) != 0) { diag.contract_errors++; set_error(&diag, "MFCC initialization failed."); goto cleanup; }
    for (;;) {
        size_t read_bytes = fread(buffer, 1u, chunk*2u, pcm), j;
        if (ferror(pcm)) { diag.io_errors++; set_error(&diag, "PCM input read failed."); break; }
        if (read_bytes & 1u) { diag.contract_errors++; set_error(&diag, "PCM input has an odd byte count."); break; }
        for (j = 0; j < read_bytes; j += 2u) {
            unsigned raw = (unsigned)buffer[j] | ((unsigned)buffer[j+1u] << 8);
            int32_t signed_value = raw >= 32768u ? (int32_t)raw - 65536 : (int32_t)raw;
            int16_t sample = (int16_t)signed_value;
            float normalized = (float)sample / 32768.0f, preemphasis;
            int ready = mfcc_push(state, sample, frame, &preemphasis);
            if (ready < 0) { diag.contract_errors++; set_error(&diag, "MFCC core rejected sample or detected invalid/nonfinite arithmetic."); break; }
            samples++;
            if (!isfinite(preemphasis)) { diag.nonfinite_detected++; set_error(&diag, "Nonfinite preemphasis output."); break; }
            if (write_float32(&streams[INPUT_FLOAT], &normalized, 1u) || write_float32(&streams[PREEMPHASIS], &preemphasis, 1u)) {
                diag.io_errors++; set_error(&diag, "Per-sample stage output write failed."); break;
            }
            if (ready) {
                if (frame->frame_id != frames || frame->start_sample != frames*MFCC_FRAME_STEP) {
                    diag.contract_errors++; set_error(&diag, "Core frame IDs or start sample ordering violated the contract."); break;
                }
                if (!frame_finite(frame)) { diag.nonfinite_detected++; set_error(&diag, "Nonfinite value in a frame stage."); break; }
                if (dump_frame(streams, frame)) { diag.io_errors++; set_error(&diag, "Frame stage output write failed."); break; }
                frames++;
            }
        }
        if (diag.message || read_bytes < chunk*2u) break;
    }
    if (!diag.message) {
        uint64_t expected = samples < MFCC_FRAME_LENGTH ? 0u : 1u + (samples - MFCC_FRAME_LENGTH)/MFCC_FRAME_STEP;
        if (frames != expected || state->samples_seen != samples || state->frames_emitted != frames) {
            diag.contract_errors++; set_error(&diag, "Final sample/frame counters violated the framing contract.");
        }
    }
cleanup:
    if (pcm && fclose(pcm) != 0) { diag.io_errors++; set_error(&diag, "PCM input close failed."); }
    for (i = 0; i < STREAM_COUNT; ++i) {
        if (streams[i].file && fclose(streams[i].file) != 0) { diag.io_errors++; set_error(&diag, "Stage output flush/close failed."); }
    }
    free(frame); free(state); free(buffer);
    if (write_manifest(output, input, streams, &diag, samples, frames, chunk, native_little, runtime_rounding) != 0) {
        set_error(&diag, "Manifest write failed; no success claim is available.");
    }
    if (diag.message) return 1;
    printf("MFCC host numerical run passed: samples=%" PRIu64 " frames=%" PRIu64 " chunk=%zu; no timing benchmark\n", samples, frames, chunk);
    return 0;
}
