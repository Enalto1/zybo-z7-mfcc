/* Transport model, not an arithmetic replacement for RTL/model comparison. */
#include "mfcc_accel.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); exit(1); } } while (0)

typedef struct {
    uint32_t count,written,consumed,captured,popped,polls,errors;
    uint32_t busy,done,input_pending,output_pending,read_mask;
    uint32_t lo,hi,frame,meta,starts,aborts,writes,done_with_pending;
    uint32_t wrong_id,freeze,stopped_timer,inject_error,corrupt,bad_counts;
    uint32_t read_failure,write_failure,rollover,cycle_hi_reads;
    uint64_t ticks,cycles;
    const int16_t *pcm;
} mock;

static int64_t value_at(uint32_t index)
{
    switch (index%5U) {
        case 0: return INT64_C(0);
        case 1: return INT64_C(1);
        case 2: return -INT64_C(1);
        case 3: return INT64_C(549755813887);
        default: return -INT64_C(549755813888);
    }
}

static void progress(mock *m)
{
    uint32_t expected=mfcc_accel_frames(m->count)*13U;
    ++m->polls;
    if (m->inject_error != 0U && m->polls==10U) m->errors=8U;
    if (m->busy == 0U || m->freeze != 0U) return;
    ++m->cycles;
    /* An occupied output slot stops PCM consumption: feed-only will deadlock. */
    if (m->input_pending != 0U && m->output_pending == 0U && m->polls%3U==0U) {
        m->input_pending=0; ++m->consumed;
    }
    if (m->output_pending==0U && m->captured<expected
        && m->consumed>=512U+(m->captured/13U)*160U) {
        uint32_t index=m->captured%13U;
        int32_t bfp=(int32_t)((m->captured/13U)%27U)-2;
        uint64_t raw=(uint64_t)value_at(m->captured);
        m->lo=(uint32_t)raw; m->hi=(uint32_t)(raw>>32);
        m->frame=m->captured/13U;
        m->meta=index|((uint32_t)(uint8_t)bfp<<8)|((uint32_t)(index==12U)<<16);
        if (m->captured==1U) {
            if (m->corrupt==1U) ++m->frame;
            if (m->corrupt==2U) m->meta^=1U;
            if (m->corrupt==3U) m->meta^=UINT32_C(0x10000);
            if (m->corrupt==4U) m->meta^=UINT32_C(0x100);
            if (m->corrupt==5U) { m->meta|=UINT32_C(0x20000); m->errors=8U; }
            if (m->corrupt==6U) m->hi=UINT32_C(0x100);
            if (m->corrupt==7U) m->meta|=UINT32_C(0x10);
            if (m->corrupt==8U) m->meta=(m->meta&~UINT32_C(0xff00))|UINT32_C(0x1900);
        }
        m->output_pending=1; m->read_mask=0; ++m->captured;
    }
    if (m->written==m->count && m->consumed==m->count && m->captured==expected) {
        m->done=1; m->busy=0;
        if (m->output_pending != 0U) ++m->done_with_pending;
    }
}

static int mock_read(void *context, uint32_t offset, uint32_t *value)
{
    mock *m=(mock *)context;
    if (m->read_failure==offset+1U) return -1;
    switch (offset) {
        case MFCC_R_ID: *value=m->wrong_id != 0U ? 0U : UINT32_C(0x4d464343); break;
        case MFCC_R_ABI: *value=UINT32_C(0x00010000); break;
        case MFCC_R_CORE_ID: *value=1; break;
        case MFCC_R_FORMAT: *value=UINT32_C(0x00011828); break;
        case MFCC_R_FRAME_LENGTH: *value=512; break;
        case MFCC_R_HOP: *value=160; break;
        case MFCC_R_NCOEF: *value=13; break;
        case MFCC_R_MAX_SAMPLES: *value=262144; break;
        case MFCC_R_CONTRACT_TAG: *value=MFCC_ACCEL_CONTRACT_TAG; break;
        case MFCC_R_STATUS:
            progress(m);
            *value=m->busy|(m->done<<1)|((uint32_t)(m->errors!=0U)<<2)
                |((uint32_t)(m->busy!=0U && m->input_pending==0U && m->written<m->count)<<3)
                |(m->output_pending<<4); break;
        case MFCC_R_OUT_LO: CHECK(m->output_pending); m->read_mask|=1U; *value=m->lo; break;
        case MFCC_R_OUT_HI: CHECK(m->output_pending); m->read_mask|=2U; *value=m->hi; break;
        case MFCC_R_OUT_FRAME: CHECK(m->output_pending); m->read_mask|=4U; *value=m->frame; break;
        case MFCC_R_OUT_META: CHECK(m->output_pending); m->read_mask|=8U; *value=m->meta; break;
        case MFCC_R_ERRORS: *value=m->errors; break;
        case MFCC_R_WRITTEN: *value=m->written; break;
        case MFCC_R_CONSUMED: *value=m->consumed-(uint32_t)(m->bad_counts!=0U && m->consumed>0U); break;
        case MFCC_R_CAPTURED: *value=m->captured; break;
        case MFCC_R_POPPED: *value=m->popped; break;
        case MFCC_R_CYCLES_HI:
            if (m->rollover != 0U) {
                *value=m->cycle_hi_reads==0U ? 0U : 1U; ++m->cycle_hi_reads;
            } else *value=(uint32_t)(m->cycles>>32);
            break;
        case MFCC_R_CYCLES_LO: *value=m->rollover != 0U ? 7U : (uint32_t)m->cycles; break;
        default: CHECK(0); return -1;
    }
    return 0;
}

static int mock_write(void *context, uint32_t offset, uint32_t value)
{
    mock *m=(mock *)context;
    if (m->write_failure==offset+1U) return -1;
    ++m->writes;
    if (offset==MFCC_R_CONTROL && value==MFCC_CMD_ABORT) {
        ++m->aborts; m->written=0; m->consumed=0; m->captured=0; m->popped=0;
        m->busy=0; m->done=0; m->errors=0; m->input_pending=0; m->output_pending=0;
        m->cycles=0; m->polls=0; m->cycle_hi_reads=0;
    } else if (offset==MFCC_R_CONTROL && value==MFCC_CMD_START) {
        CHECK(m->busy==0U && m->output_pending==0U); ++m->starts; m->busy=1;
    } else if (offset==MFCC_R_SAMPLE_COUNT) {
        CHECK(m->busy==0U && value<=262144U); m->count=value;
    } else if (offset==MFCC_R_PCM) {
        CHECK(m->busy!=0U && m->input_pending==0U && m->written<m->count);
        CHECK(value==(uint32_t)(uint16_t)m->pcm[m->written]);
        ++m->written; m->input_pending=1;
    } else if (offset==MFCC_R_POP) {
        CHECK(value==1U && m->output_pending!=0U && m->read_mask==15U);
        m->output_pending=0; ++m->popped;
    } else { CHECK(0); return -1; }
    return 0;
}

static uint64_t mock_ticks(void *context)
{
    mock *m=(mock *)context;
    if (m->stopped_timer==0U) ++m->ticks;
    return m->ticks;
}

static mfcc_accel_bus bus_for(mock *m)
{
    mfcc_accel_bus bus={m,mock_read,mock_write,mock_ticks};
    return bus;
}

static void successful_clip(uint32_t count, uint64_t initial_ticks, uint32_t rollover)
{
    mock m={0}; mfcc_accel_bus bus=bus_for(&m); mfcc_accel_stats stats;
    mfcc_accel_limits limits={UINT64_C(5000000),UINT32_C(5000000)};
    uint32_t expected=mfcc_accel_frames(count)*13U, i;
    int16_t *pcm=(int16_t *)calloc(count==0U ? 1U : count,sizeof(*pcm));
    mfcc_accel_record *records=(mfcc_accel_record *)calloc(expected+1U,sizeof(*records));
    CHECK(pcm!=NULL && records!=NULL);
    for (i=0; i<count; ++i) pcm[i]=(int16_t)((int32_t)(i%65536U)-32768);
    m.pcm=pcm; m.ticks=initial_ticks; m.rollover=rollover;
    records[expected].value_q24=INT64_C(12345);
    CHECK(mfcc_accel_run(&bus,pcm,count,records,expected,&limits,&stats)==MFCC_ACCEL_OK);
    CHECK(stats.samples_sent==count && stats.records_received==expected);
    CHECK(stats.input_written==count && stats.input_consumed==count);
    CHECK(stats.output_captured==expected && stats.output_popped==expected);
    CHECK(m.aborts==1U && m.starts==1U && m.output_pending==0U);
    CHECK(records[expected].value_q24==INT64_C(12345));
    /* With no discarded tail, the final output and DONE coexist. A trailing
       incomplete frame instead completes after that output was already popped. */
    if (expected!=0U && (count-512U)%160U==0U) CHECK(m.done_with_pending!=0U);
    if (expected!=0U && (count-512U)%160U!=0U) CHECK(m.done_with_pending==0U);
    for (i=0; i<expected; ++i) {
        CHECK(records[i].value_q24==value_at(i));
        CHECK(records[i].frame==i/13U && records[i].index==i%13U);
        CHECK(records[i].bfp_s==(int32_t)((i/13U)%27U)-2);
        CHECK(records[i].flags==(uint32_t)(i%13U==12U));
    }
    if (rollover != 0U) {
        CHECK(stats.hardware_busy_cycles==UINT64_C(0x100000007));
        CHECK(m.cycle_hi_reads==4U);
    }
    free(pcm); free(records);
}

static void faults(void)
{
    int16_t pcm[672]={0}; mfcc_accel_record records[26]; mfcc_accel_stats stats;
    mfcc_accel_limits limits={UINT64_C(100000),UINT32_C(100000)};
    uint32_t corruption;
    for (corruption=1; corruption<=8U; ++corruption) {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm; m.corrupt=corruption;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)
              ==(corruption==5U ? MFCC_ACCEL_HARDWARE : MFCC_ACCEL_SEQUENCE));
        CHECK(stats.abort_attempted==1U && m.aborts==2U && m.busy==0U);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm; m.inject_error=1;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)==MFCC_ACCEL_HARDWARE);
        CHECK(stats.error_flags==8U && m.errors==0U && stats.abort_attempted==1U);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm; m.bad_counts=1;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)==MFCC_ACCEL_SEQUENCE);
        CHECK(stats.input_consumed==671U);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm; m.wrong_id=1;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)==MFCC_ACCEL_IDENTITY);
        CHECK(m.writes==0U && stats.failed_offset==MFCC_R_ID);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm; m.read_failure=MFCC_R_STATUS+1U;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)==MFCC_ACCEL_IO);
        CHECK(stats.abort_attempted==1U && m.aborts==2U);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm; m.write_failure=MFCC_R_PCM+1U;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)==MFCC_ACCEL_IO);
        CHECK(stats.abort_attempted==1U && m.aborts==2U);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); mfcc_accel_limits short_limit={100U,1000U};
        m.pcm=pcm; m.freeze=1;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&short_limit,&stats)==MFCC_ACCEL_TIMEOUT);
        CHECK(stats.elapsed_ticks>=100U && stats.abort_attempted==1U);
        m.freeze=0;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&limits,&stats)==MFCC_ACCEL_OK);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); mfcc_accel_limits short_limit={100U,32U};
        m.pcm=pcm; m.freeze=1; m.stopped_timer=1;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,26,&short_limit,&stats)==MFCC_ACCEL_TIMEOUT);
        CHECK(stats.polls==32U && stats.elapsed_ticks==0U);
    }
    {
        mock m={0}; mfcc_accel_bus bus=bus_for(&m); m.pcm=pcm;
        CHECK(mfcc_accel_run(&bus,pcm,672,records,25,&limits,&stats)==MFCC_ACCEL_ARGUMENT);
        CHECK(mfcc_accel_run(&bus,NULL,672,records,26,&limits,&stats)==MFCC_ACCEL_ARGUMENT);
        CHECK(mfcc_accel_run(&bus,pcm,262145,records,26,&limits,&stats)==MFCC_ACCEL_ARGUMENT);
        CHECK(m.writes==0U);
    }
}

int main(void)
{
    int64_t decoded=0;
    CHECK(mfcc_accel_decode_q40(0,UINT32_C(0x80),&decoded)==MFCC_ACCEL_SEQUENCE);
    CHECK(mfcc_accel_decode_q40(0,UINT32_C(0xffffff80),&decoded)==0);
    CHECK(decoded==-INT64_C(549755813888));
    CHECK(mfcc_accel_decode_q40(UINT32_MAX,UINT32_C(0x7f),&decoded)==0);
    CHECK(decoded==INT64_C(549755813887));
    successful_clip(0,0,0); successful_clip(1,0,0); successful_clip(511,0,0);
    successful_clip(512,UINT64_MAX-4U,1); successful_clip(672,0,0);
    successful_clip(1280,0,0); successful_clip(85920,0,0);
    successful_clip(262144,0,0);
    faults();
    printf("PASS ARM_ACCEL_HOST checks=%u\n",checks);
    return 0;
}
