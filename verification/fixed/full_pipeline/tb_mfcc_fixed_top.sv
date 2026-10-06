`timescale 1ns/1ps
module tb_mfcc_fixed_top;
    reg clk=0; always #5 clk=~clk;
    reg rst_n=0,clip_start=0,pcm_valid=0,pcm_last=0,ready=0;
    reg signed [15:0] pcm=0;
    wire pcm_ready,valid,last,error,clip_done;
    wire signed [39:0] mfcc;
    wire [31:0] frame_id; wire [3:0] index; wire signed [7:0] bfp_s;
    wire fv,fr; wire signed [15:0] fq; wire [31:0] ff; wire [8:0] fi; wire signed [7:0] fs;
    wire xv,xlast; wire signed [19:0] xr,xi; wire [8:0] xb;
    wire mv,mr; wire [59:0] mel; wire [4:0] mb; wire [31:0] mf; wire signed [7:0] ms;
    mfcc_fixed_top DUT(.clk(clk),.rst_n(rst_n),.i_clip_start(clip_start),
        .i_pcm_valid(pcm_valid),.o_pcm_ready(pcm_ready),.i_pcm(pcm),.i_pcm_last(pcm_last),
        .o_valid(valid),.i_ready(ready),.o_mfcc(mfcc),.o_frame(frame_id),.o_index(index),
        .o_bfp_s(bfp_s),.o_last(last),.o_error(error),.o_clip_done(clip_done),
        .o_front_valid(fv),.o_front_ready(fr),.o_front_q(fq),.o_front_frame(ff),.o_front_index(fi),.o_front_s(fs),
        .o_fft_valid(xv),.o_fft_re(xr),.o_fft_im(xi),.o_fft_bin(xb),.o_fft_last(xlast),
        .o_mel_valid(mv),.o_mel_ready(mr),.o_mel_value(mel),.o_mel_band(mb),.o_mel_frame(mf),.o_mel_s(ms));
    // Stream expected words instead of allocating over a million unused TB words.
    reg [15:0] expected_front;
    reg [39:0] expected_fft,expected_mfcc,expected_power;
    reg [59:0] expected_mel; reg [29:0] expected_log; reg [7:0] shift_mem[0:1023];
    integer pcm_fd,front_fd,fft_fd,power_fd,mel_fd,log_fd,mfcc_fd,progress_fd;
    integer samples[0:31],frames[0:31];
    integer cases,total_frames,fd,rc,c,n,pcm_base=0,frame_base=0;
    integer front_count=0,fft_count=0,power_count=0,mel_count=0,log_count=0,out_count=0;
    integer local_front=0,local_fft=0,local_power=0,local_mel=0,local_log=0,local_out=0;
    integer cycles=0,stall_cycles=0,long_stall=0,done_count=0;
    integer metrics_fd,events_fd,case_start_cycle=0;
    integer pcm_wait=0,front_wait=0,mel_wait=0,log_wait=0;
    integer mel_reads=0,mel_products=0,mel_accs=0;
    integer expected_frame,expected_index,address;
    reg checking=0,old_stall=0,long_done=0;
    reg [85:0] held;
    string run_dir;
    always @(negedge clk) begin
        if(!rst_n) begin ready=0;long_stall=0;end
        else if(checking && valid && !long_done) begin long_stall=5000;long_done=1;ready=0;end
        else if(long_stall>0) begin long_stall=long_stall-1;ready=0;end
        else ready=(cycles%37)>8;
    end
    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles%100000==0) $display("FULL_PROGRESS cycle=%0d time=%0t checking=%b case=%0d front=%0d fft=%0d mel=%0d out=%0d front_state=%0d spectral_state=%0d",cycles,$time,checking,c,front_count,fft_count,mel_count,out_count,DUT.U_FRONT.c_state,DUT.U_SPECTRAL.c_state);
        if(checking && (DUT.frame_admitted || DUT.frame_emitted || DUT.front_done || clip_done)) begin
            $fdisplay(progress_fd,"cycle=%0d case=%0d admitted=%b emitted=%b front_done=%b clip_done=%b inflight=%0d completion_state=%0d done_count=%0d",cycles,c,DUT.frame_admitted,DUT.frame_emitted,DUT.front_done,clip_done,DUT.inflight_reg,DUT.c_state,done_count);
            $fflush(progress_fd);
        end
        if(cycles>500000+total_frames*50000) $fatal(1,"pipeline watchdog case=%0d front=%0d fft=%0d mel=%0d out=%0d done=%0d inflight=%0d completion_state=%0d",c,front_count,fft_count,mel_count,out_count,done_count,DUT.inflight_reg,DUT.c_state);
        if(!rst_n || clip_start) old_stall=0;
        else if(checking) begin
            if(pcm_valid&&!pcm_ready) pcm_wait=pcm_wait+1;
            if(fv&&!fr) front_wait=front_wait+1;
            if(mv&&!mr) mel_wait=mel_wait+1;
            if(DUT.U_BACK.log_valid&&!DUT.U_BACK.log_ready) log_wait=log_wait+1;
            if(DUT.U_SPECTRAL.U_TAIL.rom_en) mel_reads=mel_reads+1;
            if(DUT.frame_admitted) $fdisplay(events_fd,"%0d,%0d,frame_admit,%0d",c,cycles,ff);
            if(fv&&fr&&fi==511) $fdisplay(events_fd,"%0d,%0d,frontend_last,%0d",c,cycles,ff);
            if(xv&&xb==0) $fdisplay(events_fd,"%0d,%0d,fft_first,%0d",c,cycles,local_fft/512);
            if(xv&&xlast) $fdisplay(events_fd,"%0d,%0d,fft_last,%0d",c,cycles,local_fft/512);
            if(mv&&mr&&mb==0) $fdisplay(events_fd,"%0d,%0d,mel_first,%0d",c,cycles,mf);
            if(mv&&mr&&mb==25) $fdisplay(events_fd,"%0d,%0d,mel_last,%0d",c,cycles,mf);
            if(valid&&ready&&index==0) $fdisplay(events_fd,"%0d,%0d,mfcc_first,%0d",c,cycles,frame_id);
            if(valid&&ready&&last) $fdisplay(events_fd,"%0d,%0d,mfcc_last,%0d",c,cycles,frame_id);
            if(old_stall && (!valid || held!=={mfcc,frame_id,index,bfp_s,last,error})) $fatal(1,"output changed while stalled");
            old_stall=valid&&!ready;held={mfcc,frame_id,index,bfp_s,last,error};
            if(old_stall) stall_cycles=stall_cycles+1;
            if(fv&&fr) begin
                address=frame_base*512+local_front;
                rc=$fscanf(front_fd,"%h",expected_front);if(rc!=1) $fatal(1,"frontend vector EOF");
                if(fq!==expected_front || ff!==32'(local_front/512) || fi!==9'(local_front%512) || fs!==shift_mem[frame_base+local_front/512])
                    $fatal(1,"frontend mismatch case=%0d sample=%0d actual=%0d expect=%0d s=%0d",c,local_front,fq,$signed(expected_front),fs);
                front_count=front_count+1;local_front=local_front+1;
            end
            if(xv) begin
                address=frame_base*512+local_fft;
                rc=$fscanf(fft_fd,"%h",expected_fft);if(rc!=1) $fatal(1,"FFT vector EOF");
                if({xr,xi}!==expected_fft || xb!==9'(local_fft%512) || xlast!==(local_fft%512==511))
                    $fatal(1,"FFT mismatch case=%0d bin=%0d actual=%0d,%0d expected=%h",c,local_fft,xr,xi,expected_fft);
                fft_count=fft_count+1;local_fft=local_fft+1;
            end
            if(DUT.U_SPECTRAL.U_TAIL.o_power_valid) begin
                address=frame_base*257+local_power;
                rc=$fscanf(power_fd,"%h",expected_power);if(rc!=1) $fatal(1,"Power vector EOF");
                if(DUT.U_SPECTRAL.U_TAIL.o_power!==expected_power || DUT.U_SPECTRAL.U_TAIL.o_power_bin!==9'(local_power%257)) $fatal(1,"Power mismatch");
                power_count=power_count+1;local_power=local_power+1;
            end
            if(mv&&mr) begin
                address=frame_base*26+local_mel;
                rc=$fscanf(mel_fd,"%h",expected_mel);if(rc!=1) $fatal(1,"Mel vector EOF");
                if(mel!==expected_mel || mf!==32'(local_mel/26) || mb!==5'(local_mel%26) || ms!==shift_mem[frame_base+local_mel/26]) $fatal(1,"Mel mismatch case=%0d item=%0d",c,local_mel);
                mel_count=mel_count+1;local_mel=local_mel+1;
            end
            if(DUT.U_BACK.log_valid&&DUT.U_BACK.log_ready) begin
                address=frame_base*26+local_log;
                rc=$fscanf(log_fd,"%h",expected_log);if(rc!=1) $fatal(1,"log vector EOF");
                if(DUT.U_BACK.log_value!==expected_log) $fatal(1,"log mismatch case=%0d item=%0d",c,local_log);
                log_count=log_count+1;local_log=local_log+1;
            end
            if(valid&&ready) begin
                address=frame_base*13+local_out;
                rc=$fscanf(mfcc_fd,"%h",expected_mfcc);if(rc!=1) $fatal(1,"MFCC vector EOF");
                if(mfcc!==expected_mfcc || frame_id!==32'(local_out/13) || index!==4'(local_out%13) || bfp_s!==shift_mem[frame_base+local_out/13] || last!==(local_out%13==12) || error!==1'b0)
                    $fatal(1,"MFCC mismatch case=%0d item=%0d actual=%0d expected=%0d error=%b",c,local_out,mfcc,$signed(expected_mfcc),error);
                out_count=out_count+1;local_out=local_out+1;
            end
            if(clip_done) done_count=done_count+1;
        end
    end
    task reset_all;
        begin
            @(negedge clk);rst_n=0;pcm_valid=0;pcm_last=0;clip_start=0;
            repeat(5) @(negedge clk);
            rst_n=1;repeat(6) @(negedge clk);
        end
    endtask
    task begin_clip(input integer empty_clip);
        begin
            @(negedge clk);clip_start=1;pcm_valid=0;pcm_last=empty_clip;
            @(negedge clk);clip_start=0;pcm_last=0;
        end
    endtask
    task send_sample(input integer address_in,input integer end_clip,input integer gaps);
        reg [15:0] raw_pcm;
        integer pcm_rc;
        begin
            pcm_rc=$fscanf(pcm_fd,"%h",raw_pcm);if(pcm_rc!=1) $fatal(1,"PCM vector EOF");
            if(gaps && address_in%79==5) begin @(negedge clk);pcm_valid=0;repeat(3) @(negedge clk);end
            @(negedge clk);pcm=raw_pcm;pcm_valid=1;pcm_last=end_clip;
            // Observe ready before the sampling edge. Reading it after posedge
            // can see the post-acceptance deassertion on the final PCM beat.
            while(!pcm_ready) @(negedge clk);
            @(posedge clk);
        end
    endtask
    initial begin
        if(!$value$plusargs("RUN=%s",run_dir)) $fatal(1,"RUN missing");
        progress_fd=$fopen({run_dir,"/progress.log"},"w");
        events_fd=$fopen({run_dir,"/stage_events.csv"},"w");
        metrics_fd=$fopen({run_dir,"/stage_metrics.csv"},"w");
        $fdisplay(events_fd,"case,cycle,event,frame");
        $fdisplay(metrics_fd,"case,frames,cycles,pcm_wait,front_wait,mel_wait,log_wait,mel_reads");
        fd=$fopen({run_dir,"/cases.txt"},"r");rc=$fscanf(fd,"%d %d",cases,total_frames);
        for(n=0;n<cases;n=n+1) rc=$fscanf(fd,"%d %d",samples[n],frames[n]);$fclose(fd);
        pcm_fd=$fopen({run_dir,"/pcm.mem"},"r");front_fd=$fopen({run_dir,"/front.mem"},"r");
        fft_fd=$fopen({run_dir,"/fft.mem"},"r");power_fd=$fopen({run_dir,"/power.mem"},"r");
        mel_fd=$fopen({run_dir,"/mel.mem"},"r");log_fd=$fopen({run_dir,"/log.mem"},"r");
        mfcc_fd=$fopen({run_dir,"/mfcc.mem"},"r");$readmemh({run_dir,"/shift.mem"},shift_mem,0,total_frames-1);
        if(!pcm_fd||!front_fd||!fft_fd||!power_fd||!mel_fd||!log_fd||!mfcc_fd) $fatal(1,"vector open failed");
        $display("FULL_BEGIN cases=%0d frames=%0d",cases,total_frames);
        reset_all();begin_clip(0);
        for(n=0;n<173;n=n+1) send_sample(n,0,0);
        rc=$fseek(pcm_fd,0,0);if(rc) $fatal(1,"PCM seek");
        reset_all();begin_clip(0);
        for(n=0;n<512;n=n+1) send_sample(n,n==511,0);
        @(negedge clk);pcm_valid=0;pcm_last=0;
        wait(xv);reset_all();rc=$fseek(pcm_fd,0,0);if(rc) $fatal(1,"PCM seek");
        checking=1;
        for(c=0;c<cases;c=c+1) begin
            case_start_cycle=cycles;pcm_wait=0;front_wait=0;mel_wait=0;log_wait=0;mel_reads=0;
            local_front=0;local_fft=0;local_power=0;local_mel=0;local_log=0;local_out=0;
            begin_clip(samples[c]==0);
            for(n=0;n<samples[c];n=n+1) send_sample(pcm_base+n,n==samples[c]-1,1);
            $fdisplay(progress_fd,"PCM_DRAINED case=%0d samples=%0d done_count=%0d",c,samples[c],done_count);$fflush(progress_fd);
            @(negedge clk);pcm_valid=0;pcm_last=0;
            wait(done_count==c+1);
            repeat(20) @(negedge clk);
            if(local_front!=frames[c]*512 || local_fft!=frames[c]*512 || local_power!=frames[c]*257 || local_mel!=frames[c]*26 || local_log!=frames[c]*26 || local_out!=frames[c]*13)
                $fatal(1,"missing/extra transactions case=%0d frames=%0d counts=%0d/%0d/%0d/%0d/%0d/%0d",c,frames[c],local_front,local_fft,local_power,local_mel,local_log,local_out);
            $display("FULL_CASE_PASS case=%0d frames=%0d cycles=%0d",c,frames[c],cycles);
            $fdisplay(metrics_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",c,frames[c],cycles-case_start_cycle,pcm_wait,front_wait,mel_wait,log_wait,mel_reads);
            $fflush(events_fd);$fflush(metrics_fd);
            pcm_base=pcm_base+samples[c];frame_base=frame_base+frames[c];
        end
        if(frame_base!=total_frames) $fatal(1,"total frames");
        fd=$fopen({run_dir,"/protocol.json"},"w");
        $fdisplay(fd,"{\"status\":\"PASS\",\"cases\":%0d,\"frames\":%0d,\"front_samples\":%0d,\"fft_complex_bins\":%0d,\"power_bins\":%0d,\"mel_values\":%0d,\"log_values\":%0d,\"mfcc_values\":%0d,\"stall_cycles\":%0d,\"cycles\":%0d,\"reset_partial_pcm\":173,\"reset_fft_inflight\":true,\"mismatches\":0}",cases,total_frames,front_count,fft_count,power_count,mel_count,log_count,out_count,stall_cycles,cycles);
        $fclose(fd);$display("FULL_PIPELINE_PASS");$finish;
    end
endmodule
