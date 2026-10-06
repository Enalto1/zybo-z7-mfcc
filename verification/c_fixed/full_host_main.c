/* File/chunk/reset adapter only. PCM -> raw13 arithmetic lives in the core. */
#if defined(_MSC_VER)
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "mfcc_fixed_full.h"
#include "c_fixed_tables.h"
#include "c_fixed_full_tables.h"
#include "c_fixed_contract.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include <fcntl.h>
#include <io.h>
#include <sys/stat.h>
#endif

static cf_full_context context;
static cf_full_state state;
static cf_full_workspace workspace;
static cf_full_output result;
static int16_t input_buffer[4096];

static FILE *exclusive(const char *path)
{
#if defined(_WIN32)
    const int fd=_open(path,_O_WRONLY|_O_CREAT|_O_EXCL|_O_BINARY,_S_IREAD|_S_IWRITE);
    FILE *file;
    if(fd==-1)return NULL;
    file=_fdopen(fd,"wb");
    if(file==NULL)(void)_close(fd);
    return file;
#else
    return fopen(path,"wbx");
#endif
}

static int get32(FILE *file,uint32_t *out)
{
    unsigned char b[4];
    if(fread(b,1,4,file)!=4)return 0;
    *out=(uint32_t)b[0]|((uint32_t)b[1]<<8U)|((uint32_t)b[2]<<16U)|((uint32_t)b[3]<<24U);
    return 1;
}

static int get16(FILE *file,int16_t *out)
{
    unsigned char b[2];uint32_t u;int32_t s;
    if(fread(b,1,2,file)!=2)return 0;
    u=(uint32_t)b[0]|((uint32_t)b[1]<<8U);
    s=u<UINT32_C(32768)?(int32_t)u:(int32_t)u-INT32_C(65536);
    *out=(int16_t)s;return 1;
}

static int put64(FILE *file,int64_t code)
{
    uint64_t u=(uint64_t)code;unsigned char b[8];unsigned i;
    for(i=0;i<8U;++i)b[i]=(unsigned char)(u>>(8U*i));
    return fwrite(b,1,8,file)==8;
}

static int frame_dump(FILE *file,uint32_t case_id)
{
    unsigned i;
    if(!put64(file,case_id)||!put64(file,result.frame_id)||!put64(file,result.start_sample)||
       !put64(file,result.spectral.bfp_shift)||!put64(file,result.spectral.power_exp2)||
       !put64(file,result.spectral.mel_exp2)||!put64(file,result.spectral.fft_overflow)||
       !put64(file,result.spectral.power_overflow)||!put64(file,result.spectral.mel_overflow)||
       !put64(file,result.input_clips)||!put64(file,result.bfp_clamped))return 0;
    for(i=0;i<CF_N;++i)if(!put64(file,result.windowed_q30[i]))return 0;
    for(i=0;i<CF_N;++i)if(!put64(file,result.fft_input[i]))return 0;
    for(i=0;i<CF_N;++i)if(!put64(file,result.spectral.fft_re[i]))return 0;
    for(i=0;i<CF_N;++i)if(!put64(file,result.spectral.fft_im[i]))return 0;
    /* Logical unsigned40/60 are below INT64_MAX; conversion is representable. */
    for(i=0;i<CF_BINS;++i)if(!put64(file,(int64_t)result.spectral.power[i]))return 0;
    for(i=0;i<CF_MELS;++i)if(!put64(file,(int64_t)result.spectral.mel[i]))return 0;
    for(i=0;i<CF_MELS;++i)if(!put64(file,result.log_q24[i]))return 0;
    for(i=0;i<CF_MELS;++i)if(!put64(file,result.floor[i]))return 0;
    for(i=0;i<CF_FULL_COEFFICIENTS;++i)if(!put64(file,result.mfcc_q24[i]))return 0;
    return 1;
}

static int state_dump(FILE *file,uint32_t case_id)
{
    unsigned i;
    if(!put64(file,case_id)||!put64(file,state.samples_seen)||!put64(file,state.frames_emitted)||
       !put64(file,state.previous_pcm)||!put64(file,state.write_index)||!put64(file,state.samples_until_frame)||
       !put64(file,state.sticky_status)||!put64(file,state.finished)||!put64(file,state.ready!=0U))return 0;
    for(i=0;i<CF_N;++i)if(!put64(file,state.pre_ring[i]))return 0;
    return 1;
}

int main(int argc,char **argv)
{
    FILE *input=NULL,*pre=NULL,*frames=NULL,*states=NULL;
    unsigned char magic[8];uint32_t case_count,case_id,total_frames=0,total_samples=0;
    int mode,status=1;
    const unsigned chunks[]={1U,159U,160U,511U,512U,17U,672U,3U};
    if(argc!=6){fprintf(stderr,"usage: full_host input pre frames states mode0..4\n");return 2;}
    mode=atoi(argv[5]);if(mode<0||mode>4)return 2;
    input=fopen(argv[1],"rb");if(!input){perror("input");goto done;}
    if(fread(magic,1,8,input)!=8||memcmp(magic,"CFUL0002",8)!=0||!get32(input,&case_count)||case_count>1000U){fprintf(stderr,"bad header\n");goto done;}
    pre=exclusive(argv[2]);frames=exclusive(argv[3]);states=exclusive(argv[4]);
    if(!pre||!frames||!states){perror("output");goto done;}
    if(cf_full_init(&context,&c_fixed_tables,&c_fixed_full_tables)!=CF_FULL_OK){fprintf(stderr,"init failed\n");goto done;}
    for(case_id=0;case_id<case_count;++case_id){
        uint32_t samples,used=0,chunk_id=0,produced=0,seen_frames=0;
        if(!get32(input,&samples)||samples>10000000U){fprintf(stderr,"bad sample count\n");goto done;}
        if(cf_full_reset(&state)!=CF_FULL_OK)goto done;
        if(mode>=3){
            unsigned i,limit=mode==3?173U:600U;
            for(i=0;i<limit;++i)if(cf_full_push(&context,&state,(int16_t)((i&1U)?32767:-32768),&workspace,&result,&produced,NULL)!=CF_FULL_OK)goto done;
            if(cf_full_reset(&state)!=CF_FULL_OK)goto done;
        }
        while(used<samples){
            unsigned i,n=mode==0?4096U:(mode==1?1U:chunks[chunk_id%8U]);
            if(n>samples-used)n=(unsigned)(samples-used);
            for(i=0;i<n;++i)if(!get16(input,&input_buffer[i])){fprintf(stderr,"truncated PCM\n");goto done;}
            for(i=0;i<n;++i){
                int32_t pre_value=0;
                cf_full_status rc=cf_full_push(&context,&state,input_buffer[i],&workspace,&result,&produced,&pre_value);
                if(rc!=CF_FULL_OK){fprintf(stderr,"case %" PRIu32 " sample %" PRIu32 " error %d\n",case_id,used+(uint32_t)i,(int)rc);goto done;}
                if(!put64(pre,pre_value))goto done;
                if(produced){if(!frame_dump(frames,case_id))goto done;++seen_frames;}
            }
            used+=(uint32_t)n;++chunk_id;
        }
        if(seen_frames!=(samples<512U?0U:1U+(samples-512U)/160U)){fprintf(stderr,"frame count\n");goto done;}
        if(cf_full_finish(&state)!=CF_FULL_OK||cf_full_finish(&state)!=CF_FULL_OK)goto done;
        {
            cf_full_state saved;
            memcpy(&saved,&state,sizeof saved);
            if(cf_full_push(&context,&state,0,&workspace,&result,&produced,NULL)!=CF_FULL_FINISHED||
               memcmp(&saved,&state,sizeof state)!=0){fprintf(stderr,"post-finish state changed\n");goto done;}
        }
        if(!state_dump(states,case_id))goto done;
        total_frames+=seen_frames;total_samples+=samples;
    }
    if(fgetc(input)!=EOF||ferror(input)){fprintf(stderr,"trailing input\n");goto done;}
    printf("{\"cases\":%" PRIu32 ",\"frames\":%" PRIu32 ",\"samples\":%" PRIu32 ",\"mode\":%d,"
           "\"contract_version\":\"%s\",\"contract_sha256\":\"%s\",\"state_bytes\":%zu,"
           "\"workspace_bytes\":%zu,\"output_bytes\":%zu,\"context_bytes\":%zu,\"full_tables_bytes\":%zu,\"spectral_tables_bytes\":%zu}\n",
           case_count,total_frames,total_samples,mode,C_FIXED_CONTRACT_VERSION,C_FIXED_CONTRACT_SHA256,
           sizeof state,sizeof workspace,sizeof result,sizeof context,sizeof c_fixed_full_tables,sizeof c_fixed_tables);
    status=0;
done:
    if(input&&fclose(input)!=0)status=1;
    if(pre&&fclose(pre)!=0)status=1;
    if(frames&&fclose(frames)!=0)status=1;
    if(states&&fclose(states)!=0)status=1;
    return status;
}
