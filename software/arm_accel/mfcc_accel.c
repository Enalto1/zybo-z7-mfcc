#include "mfcc_accel.h"

#include <limits.h>
#include <string.h>

static int valid_bus(const mfcc_accel_bus *bus)
{
    return bus != NULL && bus->read32 != NULL && bus->write32 != NULL
        && bus->ticks != NULL;
}

uint32_t mfcc_accel_frames(uint32_t samples)
{
    return samples < MFCC_ACCEL_FRAME_LENGTH ? 0U
        : 1U+(samples-MFCC_ACCEL_FRAME_LENGTH)/MFCC_ACCEL_HOP;
}

int mfcc_accel_probe(const mfcc_accel_bus *bus, uint32_t *failed_offset)
{
    static const uint32_t identity[][2] = {
        {MFCC_R_ID, UINT32_C(0x4d464343)},
        {MFCC_R_ABI, UINT32_C(0x00010000)},
        {MFCC_R_CORE_ID, UINT32_C(1)},
        {MFCC_R_FORMAT, UINT32_C(0x00011828)},
        {MFCC_R_FRAME_LENGTH, MFCC_ACCEL_FRAME_LENGTH},
        {MFCC_R_HOP, MFCC_ACCEL_HOP}, {MFCC_R_NCOEF, MFCC_ACCEL_COEFFICIENTS},
        {MFCC_R_MAX_SAMPLES, MFCC_ACCEL_MAX_SAMPLES},
        {MFCC_R_CONTRACT_TAG, MFCC_ACCEL_CONTRACT_TAG}
    };
    size_t i;
    if (!valid_bus(bus) || failed_offset == NULL) return MFCC_ACCEL_ARGUMENT;
    *failed_offset=UINT32_MAX;
    for (i=0; i<sizeof(identity)/sizeof(identity[0]); ++i) {
        uint32_t value=0;
        if (bus->read32(bus->context,identity[i][0],&value) != 0) {
            *failed_offset=identity[i][0]; return MFCC_ACCEL_IO;
        }
        if (value != identity[i][1]) {
            *failed_offset=identity[i][0]; return MFCC_ACCEL_IDENTITY;
        }
    }
    return MFCC_ACCEL_OK;
}

int mfcc_accel_decode_q40(uint32_t low, uint32_t high, int64_t *value)
{
    const uint64_t mask=UINT64_C(0xffffffffff);
    const uint64_t raw=((uint64_t)high<<32)|(uint64_t)low;
    const int negative=(raw&UINT64_C(0x8000000000)) != 0U;
    if (value == NULL) return MFCC_ACCEL_ARGUMENT;
    if ((raw>>40) != (negative ? UINT64_C(0xffffff) : UINT64_C(0)))
        return MFCC_ACCEL_SEQUENCE;
    if (negative) {
        /* Magnitude <=2^39, so the cast and unary minus are both representable. */
        const uint64_t magnitude=((~raw)&mask)+UINT64_C(1);
        *value=-(int64_t)magnitude;
    } else *value=(int64_t)(raw&mask);
    return MFCC_ACCEL_OK;
}

static int read_word(const mfcc_accel_bus *bus, uint32_t offset,
                     uint32_t *value, mfcc_accel_stats *stats)
{
    if (bus->read32(bus->context,offset,value) == 0) return MFCC_ACCEL_OK;
    stats->failed_offset=offset; return MFCC_ACCEL_IO;
}

static int write_word(const mfcc_accel_bus *bus, uint32_t offset,
                      uint32_t value, mfcc_accel_stats *stats)
{
    if (bus->write32(bus->context,offset,value) == 0) return MFCC_ACCEL_OK;
    stats->failed_offset=offset; return MFCC_ACCEL_IO;
}

static int read_counters(const mfcc_accel_bus *bus, mfcc_accel_stats *stats)
{
    if (read_word(bus,MFCC_R_ERRORS,&stats->error_flags,stats) != 0
        || read_word(bus,MFCC_R_WRITTEN,&stats->input_written,stats) != 0
        || read_word(bus,MFCC_R_CONSUMED,&stats->input_consumed,stats) != 0
        || read_word(bus,MFCC_R_CAPTURED,&stats->output_captured,stats) != 0
        || read_word(bus,MFCC_R_POPPED,&stats->output_popped,stats) != 0)
        return MFCC_ACCEL_IO;
    return MFCC_ACCEL_OK;
}

static int read_cycles(const mfcc_accel_bus *bus, mfcc_accel_stats *stats)
{
    uint32_t attempt;
    for (attempt=0; attempt<8U; ++attempt) {
        uint32_t high1=0, low=0, high2=0;
        if (read_word(bus,MFCC_R_CYCLES_HI,&high1,stats) != 0
            || read_word(bus,MFCC_R_CYCLES_LO,&low,stats) != 0
            || read_word(bus,MFCC_R_CYCLES_HI,&high2,stats) != 0)
            return MFCC_ACCEL_IO;
        if (high1 == high2) {
            stats->hardware_busy_cycles=((uint64_t)high1<<32)|(uint64_t)low;
            return MFCC_ACCEL_OK;
        }
    }
    return MFCC_ACCEL_TIMEOUT;
}

static int fail_and_abort(const mfcc_accel_bus *bus, mfcc_accel_stats *stats,
                         uint64_t started, int result)
{
    /* Preserve diagnostics before ABORT clears the hardware's sticky state. */
    (void)read_counters(bus,stats);
    stats->elapsed_ticks=bus->ticks(bus->context)-started;
    stats->abort_attempted=1U;
    if (bus->write32(bus->context,MFCC_R_CONTROL,MFCC_CMD_ABORT) != 0)
        stats->abort_failed=1U;
    return result;
}

int mfcc_accel_run(const mfcc_accel_bus *bus, const int16_t *pcm,
                   uint32_t samples, mfcc_accel_record *records,
                   size_t record_capacity, const mfcc_accel_limits *limits,
                   mfcc_accel_stats *stats)
{
    uint32_t expected, frame_bfp=0;
    uint64_t started;
    int result;
    if (!valid_bus(bus) || limits == NULL || stats == NULL
        || samples > MFCC_ACCEL_MAX_SAMPLES
        || (samples != 0U && pcm == NULL)
        || limits->timeout_ticks == 0U || limits->timeout_ticks > (uint64_t)INT64_MAX
        || limits->max_polls == 0U) return MFCC_ACCEL_ARGUMENT;
    expected=mfcc_accel_frames(samples)*MFCC_ACCEL_COEFFICIENTS;
    if ((expected != 0U && records == NULL) || record_capacity < (size_t)expected)
        return MFCC_ACCEL_ARGUMENT;
    memset(stats,0,sizeof(*stats));
    stats->failed_offset=UINT32_MAX;
    stats->expected_frames=mfcc_accel_frames(samples);
    result=mfcc_accel_probe(bus,&stats->failed_offset);
    if (result != 0) return result; /* Never write to an unidentified peripheral. */
    started=bus->ticks(bus->context);
    if (write_word(bus,MFCC_R_CONTROL,MFCC_CMD_ABORT,stats) != 0
        || write_word(bus,MFCC_R_SAMPLE_COUNT,samples,stats) != 0
        || write_word(bus,MFCC_R_CONTROL,MFCC_CMD_START,stats) != 0)
        return fail_and_abort(bus,stats,started,MFCC_ACCEL_IO);
    for (;;) {
        uint32_t status=0;
        stats->elapsed_ticks=bus->ticks(bus->context)-started;
        if (stats->polls >= limits->max_polls
            || stats->elapsed_ticks >= limits->timeout_ticks)
            return fail_and_abort(bus,stats,started,MFCC_ACCEL_TIMEOUT);
        ++stats->polls;
        if (read_word(bus,MFCC_R_STATUS,&status,stats) != 0)
            return fail_and_abort(bus,stats,started,MFCC_ACCEL_IO);
        stats->status=status;
        if ((status&~UINT32_C(31)) != 0U)
            return fail_and_abort(bus,stats,started,MFCC_ACCEL_SEQUENCE);

        /* Drain first: feeding an entire clip before draining can deadlock. */
        if ((status&MFCC_S_OUTPUT_VALID) != 0U) {
            mfcc_accel_record record;
            uint32_t meta, index, bfp;
            if (read_word(bus,MFCC_R_OUT_LO,&stats->last_lo,stats) != 0
                || read_word(bus,MFCC_R_OUT_HI,&stats->last_hi,stats) != 0
                || read_word(bus,MFCC_R_OUT_FRAME,&stats->last_frame,stats) != 0
                || read_word(bus,MFCC_R_OUT_META,&stats->last_meta,stats) != 0)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_IO);
            meta=stats->last_meta; index=meta&15U; bfp=(meta>>8)&255U;
            record.frame=stats->last_frame; record.index=index;
            record.bfp_s=bfp>=128U ? (int32_t)bfp-256 : (int32_t)bfp;
            record.flags=(meta>>16)&3U;
            if ((record.flags&2U) != 0U)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_HARDWARE);
            if (stats->records_received >= expected
                || (meta&~UINT32_C(0x0003ff0f)) != 0U
                || record.frame != stats->records_received/MFCC_ACCEL_COEFFICIENTS
                || index != stats->records_received%MFCC_ACCEL_COEFFICIENTS
                || (record.flags&1U) != (uint32_t)(index==12U)
                || record.bfp_s < -2 || record.bfp_s > 24
                || (index != 0U && bfp != frame_bfp)
                || mfcc_accel_decode_q40(stats->last_lo,stats->last_hi,&record.value_q24) != 0)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_SEQUENCE);
            frame_bfp=bfp;
            records[stats->records_received]=record;
            if (write_word(bus,MFCC_R_POP,1U,stats) != 0)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_IO);
            ++stats->records_received;
        }
        if ((status&MFCC_S_ERROR) != 0U)
            return fail_and_abort(bus,stats,started,MFCC_ACCEL_HARDWARE);
        if ((status&MFCC_S_PCM_READY) != 0U && stats->samples_sent < samples) {
            const uint32_t bits=(uint32_t)(uint16_t)pcm[stats->samples_sent];
            if (write_word(bus,MFCC_R_PCM,bits,stats) != 0)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_IO);
            ++stats->samples_sent;
        }
        if ((status&MFCC_S_DONE) != 0U) {
            if (read_word(bus,MFCC_R_STATUS,&stats->status,stats) != 0
                || read_counters(bus,stats) != 0)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_IO);
            if ((stats->status&MFCC_S_ERROR) != 0U || stats->error_flags != 0U)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_HARDWARE);
            if ((stats->status&MFCC_S_DONE) == 0U
                || (stats->status&(MFCC_S_BUSY|MFCC_S_OUTPUT_VALID)) != 0U
                || stats->samples_sent != samples || stats->records_received != expected
                || stats->input_written != samples || stats->input_consumed != samples
                || stats->output_captured != expected || stats->output_popped != expected)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_SEQUENCE);
            result=read_cycles(bus,stats);
            if (result != 0) return fail_and_abort(bus,stats,started,result);
            stats->elapsed_ticks=bus->ticks(bus->context)-started;
            if (stats->elapsed_ticks >= limits->timeout_ticks)
                return fail_and_abort(bus,stats,started,MFCC_ACCEL_TIMEOUT);
            return MFCC_ACCEL_OK;
        }
    }
}
