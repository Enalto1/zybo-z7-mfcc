#include "mfcc_fixed_full.h"
#include "c_fixed_tables.h"
#include "c_fixed_full_tables.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

static unsigned checks, failures;
static cf_full_context context;
static cf_full_state state, other_state;
static cf_full_workspace work;
static cf_full_output output, saved_output;
static cf_full_tables altered;
static int32_t windowed[CF_N], logs[CF_MELS];
static int16_t codes[CF_N];
static int64_t dct_output[CF_FULL_COEFFICIENTS];

static void check(int condition, const char *description)
{
    ++checks;
    if (!condition) {
        if (failures < 30) fprintf(stderr, "FAIL: %s\n", description);
        ++failures;
    }
}

static void arithmetic_boundaries(void)
{
    int32_t value, shift;
    uint32_t flag, clips;
    unsigned n;
    check(cf_full_preemphasis(1, 0, &value) == CF_FULL_OK && value == 32768, "first PCM1");
    check(cf_full_preemphasis(-1, 0, &value) == CF_FULL_OK && value == -32768, "first PCM-1");
    check(cf_full_preemphasis(1, 1, &value) == CF_FULL_OK && value == 1638, "continuous DC1");
    check(cf_full_preemphasis(0, 1, &value) == CF_FULL_OK && value == -31130, "negative preemphasis rounding");
    check(cf_full_preemphasis(0, -1, &value) == CF_FULL_OK && value == 31130, "positive preemphasis rounding");
    check(cf_full_preemphasis(INT16_MIN, 0, &value) == CF_FULL_OK && value == -1073741824,
          "PCM minimum normalization exact");
    check(cf_full_preemphasis(INT16_MAX, 0, &value) == CF_FULL_OK && value == 1073709056,
          "PCM maximum normalization exact");
    check(cf_full_preemphasis(INT16_MIN, INT16_MAX, &value) == CF_FULL_OK, "preemphasis worst negative transition");
    check(cf_full_preemphasis(INT16_MAX, INT16_MIN, &value) == CF_FULL_OK, "preemphasis worst positive transition");
    check(cf_full_preemphasis(0, 0, NULL) == CF_FULL_BAD_ARGUMENT, "preemphasis null");

    memset(windowed, 0, sizeof(windowed));
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == 0 && flag == 0, "silence BFP");
    windowed[511] = 1;
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == 24 && flag == 1, "upper BFP boundary flag");
    windowed[511] = 124;
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == 24 && flag == 1, "BFP before-round fits124");
    windowed[511] = 125;
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == 23 && flag == 0, "BFP before-round rejects125");
    windowed[511] = 31949;
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == 16, "exact integer target");
    windowed[511] = 31950;
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == 15, "target plusone");
    windowed[511] = INT32_MIN;
    check(cf_full_choose_bfp(windowed, &shift, &flag) == CF_FULL_OK && shift == -1 && flag == 0, "BFP abs INT32_MIN safe");
    check(cf_full_choose_bfp(NULL, &shift, &flag) == CF_FULL_BAD_ARGUMENT, "BFP null");

    memset(windowed, 0, sizeof(windowed));
    windowed[0] = -5; windowed[1] = -3; windowed[2] = -1;
    windowed[3] = 1; windowed[4] = 3; windowed[5] = 5;
    check(cf_full_quantize(windowed, 15, codes, &clips) == CF_FULL_OK && clips == 0 &&
          codes[0] == -2 && codes[1] == -2 && codes[2] == 0 && codes[3] == 0 && codes[4] == 2 && codes[5] == 2,
          "input negative and positive ties-to-even");
    windowed[0] = -32768;
    check(cf_full_quantize(windowed, 16, codes, &clips) == CF_FULL_OK && codes[0] == -32767 && clips == 1,
          "symmetric clamp includes negative signed16 endpoint");
    memset(windowed, 0, sizeof(windowed)); windowed[0] = INT32_MIN; windowed[1] = INT32_MAX;
    check(cf_full_quantize(windowed, 24, codes, &clips) == CF_FULL_OK && codes[0] == -32767 &&
          codes[1] == 32767 && clips == 2, "negative left-scale uses wide multiplication");
    check(cf_full_quantize(windowed, -3, codes, &clips) == CF_FULL_BAD_EXPONENT, "input exponent low");
    check(cf_full_quantize(windowed, 25, codes, &clips) == CF_FULL_BAD_EXPONENT, "input exponent high");
    check(cf_full_quantize(windowed, 0, NULL, &clips) == CF_FULL_BAD_ARGUMENT, "input quantize null");

    check(cf_full_floor(8, -43, &flag) == CF_FULL_OK && flag == 1, "positive Mel below floor");
    check(cf_full_floor(9, -43, &flag) == CF_FULL_OK && flag == 0, "first Mel above floor");
    check(cf_full_floor(UINT64_C(2475880078570760), -91, &flag) == CF_FULL_OK && flag == 1,
          "floor largest exponent below edge");
    check(cf_full_floor(UINT64_C(2475880078570761), -91, &flag) == CF_FULL_OK && flag == 0,
          "floor largest exponent above edge");
    for (shift = -91; shift <= -39; shift += 2) {
        check(cf_full_floor(0, shift, &flag) == CF_FULL_OK && flag == 1, "zero floor every exponent");
        check(cf_full_floor((UINT64_C(1) << 60) - 1, shift, &flag) == CF_FULL_OK && flag == 0,
              "unsigned60 max above floor every exponent");
    }
    check(cf_full_floor(UINT64_C(1) << 60, -43, &flag) == CF_FULL_LOG_RANGE, "Mel unsigned60 overflow");
    check(cf_full_floor(UINT64_MAX, -43, &flag) == CF_FULL_LOG_RANGE, "negative cast to uint64 rejected");
    check(cf_full_floor(1, -42, &flag) == CF_FULL_BAD_EXPONENT, "unreachable even exponent rejected");
    check(cf_full_floor(1, INT32_MIN, &flag) == CF_FULL_BAD_EXPONENT, "INT32_MIN exponent rejected before negation");

    check(cf_full_log_scale(0, &value) == CF_FULL_OK && value == 0, "log scale zero");
    check(cf_full_log_scale(INT64_C(1073741824), &value) == CF_FULL_OK && value == 11629080, "log scale ln2");
    check(cf_full_log_scale(-INT64_C(1073741824), &value) == CF_FULL_OK && value == -11629080, "log scale negative ln2");
    check(cf_full_log_scale(-(INT64_C(1) << 36), &value) == CF_FULL_OK && value == -744261118, "signed37 minimum raw transform");
    check(cf_full_log_scale((INT64_C(1) << 36) - 1, &value) == CF_FULL_OK && value == 744261118, "signed37 maximum raw transform");
    check(cf_full_log_scale(INT64_C(1) << 36, &value) == CF_FULL_LOG_RANGE, "signed37 upper rejection");
    check(cf_full_log_scale(-(INT64_C(1) << 36) - 1, &value) == CF_FULL_LOG_RANGE, "signed37 lower rejection");
    check(cf_full_log_scale(INT64_MIN, &value) == CF_FULL_LOG_RANGE, "int64 extreme rejected before wide multiply");
    check(cf_full_log(0, -43, &value, &flag) == CF_FULL_OK && flag == 1 && value == -463571610, "integer floor log");
    check(cf_full_log(UINT64_C(1) << 43, -43, &value, &flag) == CF_FULL_OK && flag == 0 && value == 0, "ln1 exact");
    check(cf_full_log(UINT64_C(1) << 44, -43, &value, &flag) == CF_FULL_OK && flag == 0 && value == 11629080, "ln2 exact model");
    check(cf_full_log(UINT64_C(1) << 42, -43, &value, &flag) == CF_FULL_OK && flag == 0 && value == -11629080, "lnhalf exact model");

    memset(logs, 0, sizeof(logs));
    check(cf_full_dct(&c_fixed_full_tables, logs, dct_output) == CF_FULL_OK, "DCT zero input accepted");
    for (n = 0; n < CF_FULL_COEFFICIENTS; ++n) check(dct_output[n] == 0, "DCT zero13");
    logs[25] = INT32_C(536870912);
    check(cf_full_dct(&c_fixed_full_tables, logs, dct_output) == CF_FULL_DCT_RANGE, "DCT logical30 input guard");
    memset(&altered, 0, sizeof(altered));
    for (n = 0; n < CF_MELS; ++n) {
        logs[n] = -INT32_C(536870912);
        altered.dct_q30[0][n] = -INT32_C(1073741824);
    }
    check(cf_full_dct(&altered, logs, dct_output) == CF_FULL_DCT_RANGE,
          "DCT checked signed64 accumulation reports overflow without wrapping");
}

static void state_and_guards(void)
{
    uint32_t produced;
    int32_t pre;
    unsigned n;
    cf_full_context invalid;
    memset(&invalid, 0, sizeof(invalid));
    check(cf_full_init(NULL, &c_fixed_tables, &c_fixed_full_tables) == CF_FULL_BAD_ARGUMENT, "null init");
    check(cf_full_init(&context, NULL, &c_fixed_full_tables) == CF_FULL_BAD_ARGUMENT && context.ready == 0, "failed init clears ready");
    altered = c_fixed_full_tables; altered.window_q30[0] = UINT32_C(2147483648);
    check(cf_full_init(&context, &c_fixed_tables, &altered) == CF_FULL_BAD_TABLE, "unsigned31 window guard");
    altered = c_fixed_full_tables; altered.dct_q30[0][0] = INT32_C(1073741824);
    check(cf_full_init(&context, &c_fixed_tables, &altered) == CF_FULL_BAD_TABLE, "signed31 DCT coefficient guard");
    check(cf_full_init(&context, &c_fixed_tables, &c_fixed_full_tables) == CF_FULL_OK, "published tables initialize");
    check(cf_full_reset(NULL) == CF_FULL_BAD_ARGUMENT, "reset null");
    memset(&state, 0, sizeof(state));
    check(cf_full_push(&context, &state, 0, &work, &output, &produced, NULL) == CF_FULL_STATE_ERROR, "unreset state");
    check(cf_full_reset(&state) == CF_FULL_OK, "initial reset");
    check(cf_full_push(&invalid, &state, 0, &work, &output, &produced, NULL) == CF_FULL_BAD_ARGUMENT, "uninitialized context");
    check(cf_full_push(&context, &state, 0, NULL, &output, &produced, NULL) == CF_FULL_BAD_ARGUMENT, "null work");
    check(cf_full_push(&context, &state, 0, &work, NULL, &produced, NULL) == CF_FULL_BAD_ARGUMENT, "null output");
    check(cf_full_push(&context, &state, 0, &work, &output, NULL, NULL) == CF_FULL_BAD_ARGUMENT, "null produced");
    check(state.samples_seen == 0 && state.previous_pcm == 0, "invalid calls do not consume");
    for (n = 0; n < 511; ++n)
        check(cf_full_push(&context, &state, 1, &work, &output, &produced, &pre) == CF_FULL_OK && produced == 0,
              "incomplete initial frame withheld");
    check(state.previous_pcm == 1 && state.samples_seen == 511 && state.frames_emitted == 0, "tail updates previous PCM");
    check(cf_full_finish(&state) == CF_FULL_OK && state.frames_emitted == 0, "finish never pads511");
    check(cf_full_finish(&state) == CF_FULL_OK, "finish idempotent");
    check(cf_full_push(&context, &state, 0, &work, &output, &produced, NULL) == CF_FULL_FINISHED && state.samples_seen == 511,
          "finish rejects additional PCM");
    cf_full_reset(&state);
    for (n = 0; n < 832; ++n) {
        const uint32_t expected = n == 511 || n == 671 || n == 831 ? 1U : 0U;
        check(cf_full_push(&context, &state, 0, &work, &output, &produced, &pre) == CF_FULL_OK && produced == expected,
              "512/160 only completed frames");
        if (produced != 0) {
            check(output.start_sample == n + 1 - 512 && output.frame_id == (n + 1 - 512) / 160,
                  "frame starts and increasing local ids");
            check(output.spectral.bfp_shift == 0 && output.input_clips == 0 && output.bfp_clamped == 0,
                  "silence metadata");
            if (n == 511) saved_output = output;
        }
    }
    check(state.frames_emitted == 3 && state.samples_seen == 832, "full clip counters");
    check(cf_full_push(&context, &state, 123, &work, &output, &produced, &pre) == CF_FULL_OK && produced == 0 &&
          pre == INT32_C(4030464) && state.previous_pcm == 123, "discarded tail is still preemphasized");
    cf_full_finish(&state);
    cf_full_reset(&state);
    for (n = 0; n < 512; ++n) cf_full_push(&context, &state, 0, &work, &output, &produced, NULL);
    check(produced == 1 && memcmp(&saved_output, &output, sizeof(output)) == 0, "reset reproduces entire frame bytes");
    cf_full_reset(&state);
    for (n = 0; n < 512; ++n) cf_full_push(&context, &state, 1, &work, &output, &produced, NULL);
    check(cf_full_push(&context, &state, 0, &work, &output, &produced, &pre) == CF_FULL_OK && pre == -31130 && produced == 0,
          "preemphasis continuity after frame boundary");
    cf_full_reset(&other_state);
    for (n = 0; n < 173; ++n) cf_full_push(&context, &other_state, 999, &work, &output, &produced, NULL);
    cf_full_reset(&other_state);
    check(cf_full_push(&context, &other_state, 1, &work, &output, &produced, &pre) == CF_FULL_OK && pre == 32768 &&
          other_state.frames_emitted == 0 && other_state.samples_seen == 1, "reset discards partial frame and previous PCM");
    check(cf_full_push(&context, &state, 1, &work, &output, &produced, &pre) == CF_FULL_OK && pre == 32768,
          "interleaved stream states independent");
    cf_full_reset(&state); state.samples_seen = UINT32_MAX;
    check(cf_full_push(&context, &state, 1, &work, &output, &produced, NULL) == CF_FULL_COUNT_RANGE && produced == 0,
          "sample counter overflow detected");
    check(cf_full_push(&context, &state, 1, &work, &output, &produced, NULL) == CF_FULL_COUNT_RANGE,
          "sample overflow sticky");

    /* Invalid arithmetic fixture inside declared coefficient widths; this is
     * never substituted for the published coefficient table. */
    altered = c_fixed_full_tables;
    for (n = 0; n < CF_N; ++n) altered.window_q30[n] = UINT32_C(2147483647);
    check(cf_full_init(&context, &c_fixed_tables, &altered) == CF_FULL_OK, "numeric-width window fixture valid");
    cf_full_reset(&state);
    for (n = 0; n < 511; ++n)
        cf_full_push(&context, &state, n % 2 == 0 ? INT16_MIN : INT16_MAX, &work, &output, &produced, NULL);
    check(cf_full_push(&context, &state, INT16_MAX, &work, &output, &produced, NULL) == CF_FULL_FRONT_RANGE && produced == 0,
          "window32 overflow reports error");
    check(cf_full_push(&context, &state, 0, &work, &output, &produced, NULL) == CF_FULL_FRONT_RANGE && state.samples_seen == 512,
          "arithmetic failure sticky without consuming next PCM");
    check(cf_full_finish(&state) == CF_FULL_FRONT_RANGE, "finish preserves numeric failure");
    check(cf_full_init(&context, &c_fixed_tables, &c_fixed_full_tables) == CF_FULL_OK && cf_full_reset(&state) == CF_FULL_OK,
          "explicit reset and reinit recover");
}

int main(void)
{
    arithmetic_boundaries();
    state_and_guards();
    printf("full_core_checks=%u failures=%u\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
