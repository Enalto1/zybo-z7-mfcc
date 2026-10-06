/* Portable file adapter for integer stage validation. No timing is measured. */
#if defined(_MSC_VER)
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "mfcc_fixed.h"
#include "c_fixed_tables.h"
#include "c_fixed_contract.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#if defined(_WIN32)
#include <fcntl.h>
#include <io.h>
#include <sys/stat.h>
#endif

static cf_workspace workspace;
static cf_result result;
static cf_fft_trace trace;
static int16_t real_input[CF_N], imag_input[CF_N];

static FILE *exclusive_output(const char *path)
{
#if defined(_WIN32)
    /* The bundled Clang's legacy Windows CRT rejects C11 fopen mode x.
     * Keep exclusive creation in the file adapter, outside the integer core. */
    const int fd = _open(path, _O_WRONLY|_O_CREAT|_O_EXCL|_O_BINARY, _S_IREAD|_S_IWRITE);
    FILE *file;
    if (fd == -1) return NULL;
    file = _fdopen(fd,"wb");
    if (file == NULL) (void)_close(fd);
    return file;
#else
    return fopen(path,"wbx");
#endif
}

static int read_u32(FILE *f, uint32_t *out)
{
    unsigned char b[4];
    if (fread(b, 1, 4, f) != 4) return 0;
    *out = (uint32_t)b[0] | ((uint32_t)b[1] << 8U) |
           ((uint32_t)b[2] << 16U) | ((uint32_t)b[3] << 24U);
    return 1;
}

static int read_i16(FILE *f, int16_t *out)
{
    unsigned char b[2];
    uint32_t u;
    int32_t s;
    if (fread(b, 1, 2, f) != 2) return 0;
    u = (uint32_t)b[0] | ((uint32_t)b[1] << 8U);
    s = u < UINT32_C(32768) ? (int32_t)u : (int32_t)u - INT32_C(65536);
    *out = (int16_t)s;
    return 1;
}

static int write_u64(FILE *f, uint64_t value)
{
    unsigned char b[8];
    unsigned i;
    for (i = 0; i < 8U; ++i) b[i] = (unsigned char)(value >> (8U*i));
    return fwrite(b, 1, 8, f) == 8;
}

static int write_i64(FILE *f, int64_t value)
{
    /* Conversion to unsigned is defined modulo 2^64, including negatives. */
    return write_u64(f, (uint64_t)value);
}

static int write_frame(FILE *f)
{
    unsigned i, g, c;
    if (!write_i64(f, result.bfp_shift) || !write_i64(f, result.power_exp2) ||
        !write_i64(f, result.mel_exp2) || !write_u64(f, result.fft_overflow) ||
        !write_u64(f, result.power_overflow) || !write_u64(f, result.mel_overflow)) return 0;
    for (i=0; i<CF_N; ++i) if (!write_i64(f,result.fft_re[i])) return 0;
    for (i=0; i<CF_N; ++i) if (!write_i64(f,result.fft_im[i])) return 0;
    for (i=0; i<CF_BINS; ++i) if (!write_u64(f,result.power[i])) return 0;
    for (i=0; i<CF_MELS; ++i) if (!write_u64(f,result.mel[i])) return 0;
    for (c=0; c<2; ++c) for (i=0; i<CF_N; ++i)
        if (!write_i64(f,trace.promoted[c][i])) return 0;
    for (g=0; g<CF_FFT_GROUPS; ++g) for (c=0; c<2; ++c) for (i=0; i<CF_N; ++i)
        if (!write_i64(f,trace.groups[g][c][i])) return 0;
    for (g=0; g<CF_FFT_GROUPS; ++g) if (!write_u64(f,trace.overflow_after_group[g])) return 0;
    return 1;
}

int main(int argc, char **argv)
{
    FILE *input = NULL, *output = NULL;
    cf_context context;
    unsigned char magic[8];
    uint32_t count, frame, bits;
    unsigned i;
    int exit_code = 1;
    if (argc != 3) { fprintf(stderr,"usage: host input.bin output_i64le.bin\n"); return 2; }
    if (strcmp(argv[1],argv[2]) == 0) { fprintf(stderr,"input/output alias\n"); return 2; }
    input = fopen(argv[1], "rb");
    if (!input) { perror("input"); goto done; }
    if (fread(magic,1,8,input)!=8 || memcmp(magic,"CFIN0001",8)!=0 ||
        !read_u32(input,&count)) { fprintf(stderr,"invalid header\n"); goto done; }
    if (count > UINT32_C(1000000)) { fprintf(stderr,"too many frames\n"); goto done; }
    if (cf_init(&context,&c_fixed_tables) != CF_OK) { fprintf(stderr,"bad tables\n"); goto done; }
    /* Refuse existing output, so a failed rerun cannot overwrite prior evidence. */
    output = exclusive_output(argv[2]);
    if (!output) { perror("output"); goto done; }
    for (frame=0; frame<count; ++frame) {
        int64_t signed_shift;
        cf_status status;
        if (!read_u32(input,&bits)) { fprintf(stderr,"truncated exponent\n"); goto done; }
        signed_shift = bits <= UINT32_C(2147483647) ? (int64_t)bits : (int64_t)bits-INT64_C(4294967296);
        for (i=0; i<CF_N; ++i) if (!read_i16(input,&real_input[i])) { fprintf(stderr,"truncated real\n"); goto done; }
        for (i=0; i<CF_N; ++i) if (!read_i16(input,&imag_input[i])) { fprintf(stderr,"truncated imag\n"); goto done; }
        status = cf_process(&context,real_input,imag_input,(int32_t)signed_shift,
                            &workspace,&result,&trace);
        if (status != CF_OK) { fprintf(stderr,"frame %" PRIu32 " status %d\n",frame,(int)status); goto done; }
        if (!write_frame(output)) { fprintf(stderr,"output write error\n"); goto done; }
    }
    if (fgetc(input)!=EOF || ferror(input)) { fprintf(stderr,"unexpected trailing input\n"); goto done; }
    printf("{\"frames\":%" PRIu32 ",\"contract_version\":\"%s\",\"contract_sha256\":\"%s\","
           "\"workspace_bytes\":%zu,\"result_bytes\":%zu,\"trace_bytes\":%zu,\"table_bytes\":%zu}\n",
           count,C_FIXED_CONTRACT_VERSION,C_FIXED_CONTRACT_SHA256,
           sizeof workspace,sizeof result,sizeof trace,sizeof c_fixed_tables);
    exit_code = 0;
done:
    if (input && fclose(input)!=0) exit_code=1;
    if (output && fclose(output)!=0) exit_code=1;
    return exit_code;
}
