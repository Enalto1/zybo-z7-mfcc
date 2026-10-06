`timescale 1ns/1ps
module tb_mfcc_fixed_frontend_pipeline;
    logic clk=0,rst_n=0,start=0,pcm_valid=0,pcm_last=0,ready=0;
    logic signed [15:0] pcm=0,fft;
    logic pcm_ready,valid,last,error,done;
    logic [31:0] frame;
    logic [8:0] index;
    logic signed [7:0] bfp;
    mfcc_fixed_frontend DUT(.clk(clk),.rst_n(rst_n),.i_clip_start(start),
        .i_pcm_valid(pcm_valid),.o_pcm_ready(pcm_ready),.i_pcm(pcm),.i_pcm_last(pcm_last),
        .o_valid(valid),.i_ready(ready),.o_fft(fft),.o_frame(frame),.o_index(index),
        .o_bfp_s(bfp),.o_last(last),.o_error(error),.o_clip_done(done));
    always #5 clk=~clk;
    string run_dir;
    integer pcm_fd,front_fd,window_fd,shift_fd,cases_fd,cycle_fd,report_fd,rc;
    integer cases,total_frames,samples[0:31],frames[0:31],c,n,p;
    integer cycles=0,outputs=0,window_writes=0,window_reads=0,pre_reads=0;
    integer done_count=0,stall_cycles=0,local_outputs=0,local_window=0;
    integer reset_checks=0,abort_checks=0,first_cycle,last_cycle;
    integer stall_remaining=0,first_stalls=0,middle_stalls=0,last_stalls=0;
    integer scan_start_cycle=-1,scan_count=0,scan_min=1000000,scan_max=0;
    logic checking=0,was_stalled=0,covered_first=0,covered_middle=0,covered_last=0;
    logic [67:0] held_output;
    logic [103:0] held_pipeline;
    logic [15:0] expected_fft,raw_pcm;
    logic [31:0] expected_window;
    logic [7:0] expected_shift;
    logic [15:0] abort_pcm[0:511];
    always @(negedge clk) begin
        if(!checking) begin ready=1;stall_remaining=0;end
        else if(stall_remaining>0) begin ready=0;stall_remaining=stall_remaining-1;end
        else if(valid&&index==9'd0&&!covered_first) begin
            ready=0;stall_remaining=36;covered_first=1;first_stalls=first_stalls+1;
        end else if(valid&&index==9'd255&&!covered_middle) begin
            ready=0;stall_remaining=52;covered_middle=1;middle_stalls=middle_stalls+1;
        end else if(valid&&index==9'd511&&!covered_last) begin
            ready=0;stall_remaining=70;covered_last=1;last_stalls=last_stalls+1;
        end else ready=(cycles%11>=3);
    end
    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>100000+total_frames*10000) $fatal(1,"frontend pipeline timeout");
        if(!rst_n||start||!checking) was_stalled=0;
        else begin
            if(was_stalled) begin
                if({valid,fft,frame,index,bfp,last,error}!==held_output) $fatal(1,"output changed under stall");
                if({DUT.issue_index_reg,DUT.issue_done_reg,DUT.drain_read_valid_reg,DUT.drain_quant_valid_reg,
                    DUT.output_valid_reg,DUT.drain_read_index_reg,DUT.drain_quant_index_reg,
                    DUT.quant_quotient_reg,DUT.quant_round_reg,DUT.window_data_reg}!==held_pipeline)
                    $fatal(1,"drain pipeline advanced under stall");
            end
            was_stalled=valid&&!ready;
            held_output={valid,fft,frame,index,bfp,last,error};
            held_pipeline={DUT.issue_index_reg,DUT.issue_done_reg,DUT.drain_read_valid_reg,DUT.drain_quant_valid_reg,
                DUT.output_valid_reg,DUT.drain_read_index_reg,DUT.drain_quant_index_reg,
                DUT.quant_quotient_reg,DUT.quant_round_reg,DUT.window_data_reg};
            if(was_stalled) stall_cycles=stall_cycles+1;
            if(DUT.pre_read) begin
                pre_reads=pre_reads+1;
                if(DUT.issue_index_reg==9'd0) scan_start_cycle=cycles;
            end
            if(DUT.window_read) window_reads=window_reads+1;
            if(DUT.window_write) begin
                rc=$fscanf(window_fd,"%h",expected_window);
                if(rc!=1||DUT.window_store_reg!==expected_window||DUT.window_magnitude_index_reg!==9'(local_window%512))
                    $fatal(1,"window mismatch case=%0d item=%0d got=%h want=%h",c,local_window,DUT.window_store_reg,expected_window);
                window_writes=window_writes+1;local_window=local_window+1;
                if(DUT.window_magnitude_index_reg==9'd511) begin
                    scan_count=cycles-scan_start_cycle+1;
                    if(scan_count!=516) $fatal(1,"window scan II1 violated clocks=%0d",scan_count);
                    if(scan_count<scan_min) scan_min=scan_count;
                    if(scan_count>scan_max) scan_max=scan_count;
                end
            end
            if(valid&&ready) begin
                rc=$fscanf(front_fd,"%h",expected_fft);
                if(local_outputs%512==0) begin
                    rc=rc+$fscanf(shift_fd,"%h",expected_shift);
                    if(rc!=2) $fatal(1,"frontend oracle EOF");
                    first_cycle=cycles;
                end else if(rc!=1) $fatal(1,"frontend oracle EOF");
                if(fft!==expected_fft||frame!==32'(local_outputs/512)||index!==9'(local_outputs%512)||
                    bfp!==expected_shift||last!==(local_outputs%512==511)||error)
                    $fatal(1,"frontend mismatch case=%0d item=%0d got=%0d/%0d/%0d/%0d error=%b want=%0d/%0d/%0d/%0d",
                        c,local_outputs,fft,frame,index,bfp,error,$signed(expected_fft),local_outputs/512,local_outputs%512,$signed(expected_shift));
                local_outputs=local_outputs+1;outputs=outputs+1;
                if(last) begin
                    last_cycle=cycles;
                    $fdisplay(cycle_fd,"%0d,%0d,%0d,%0d,%0d",c,frame,first_cycle,last_cycle,$signed(bfp));
                end
            end
            if(done) done_count=done_count+1;
        end
    end
    task pulse_reset;
        begin
            @(negedge clk);rst_n=0;pcm_valid=0;pcm_last=0;start=0;
            repeat(4) @(negedge clk);rst_n=1;repeat(2) @(negedge clk);
        end
    endtask
    task begin_clip(input bit empty);
        begin
            @(negedge clk);start=1;pcm_valid=0;pcm_last=empty;
            @(negedge clk);start=0;pcm_last=0;
        end
    endtask
    task send_pcm(input logic [15:0] value,input bit final_sample,input bit gap);
        begin
            @(negedge clk);pcm_valid=0;
            if(gap) repeat(3) @(negedge clk);
            pcm=value;pcm_valid=1;pcm_last=final_sample;
            while(!pcm_ready) @(negedge clk);
            @(posedge clk);
        end
    endtask
    task send_abort_frame;
        integer a;
        begin
            begin_clip(0);
            for(a=0;a<512;a=a+1) send_pcm(abort_pcm[a],a==511,0);
            @(negedge clk);pcm_valid=0;pcm_last=0;
        end
    endtask
    task assert_flushed;
        begin
            repeat(8) begin
                @(negedge clk);
                if(valid||DUT.window_read_valid_reg||DUT.window_product_valid_reg||
                    DUT.window_value_valid_reg||DUT.window_magnitude_valid_reg||
                    DUT.drain_read_valid_reg||DUT.drain_quant_valid_reg||DUT.output_valid_reg)
                    $fatal(1,"reset/abort left pipeline token");
            end
        end
    endtask
    initial begin
        if(!$value$plusargs("RUN=%s",run_dir)) $fatal(1,"RUN missing");
        cases_fd=$fopen({run_dir,"/cases.txt"},"r");rc=$fscanf(cases_fd,"%d %d",cases,total_frames);
        for(p=0;p<cases;p=p+1) rc=$fscanf(cases_fd,"%d %d",samples[p],frames[p]);$fclose(cases_fd);
        pcm_fd=$fopen({run_dir,"/pcm.mem"},"r");front_fd=$fopen({run_dir,"/front.mem"},"r");
        window_fd=$fopen({run_dir,"/window.mem"},"r");shift_fd=$fopen({run_dir,"/shift.mem"},"r");
        cycle_fd=$fopen({run_dir,"/frame_cycles.csv"},"w");$fdisplay(cycle_fd,"case,frame,first_output_cycle,last_output_cycle,bfp_s");
        if(!pcm_fd||!front_fd||!window_fd||!shift_fd) $fatal(1,"vector open failed");
        $readmemh({run_dir,"/abort_pcm.mem"},abort_pcm);
        pulse_reset();
        // Reset and clip-start each abort with all four window stages occupied.
        send_abort_frame();wait(DUT.window_read_valid_reg&&DUT.window_product_valid_reg&&DUT.window_value_valid_reg&&DUT.window_magnitude_valid_reg);
        pulse_reset();assert_flushed();reset_checks=reset_checks+1;
        send_abort_frame();wait(DUT.window_read_valid_reg&&DUT.window_product_valid_reg&&DUT.window_value_valid_reg&&DUT.window_magnitude_valid_reg);
        begin_clip(0);assert_flushed();abort_checks=abort_checks+1;
        // Repeat with every drain stage occupied; no remaining read may escape.
        send_abort_frame();wait(DUT.drain_read_valid_reg&&DUT.drain_quant_valid_reg&&DUT.output_valid_reg);
        pulse_reset();assert_flushed();reset_checks=reset_checks+1;
        send_abort_frame();wait(DUT.drain_read_valid_reg&&DUT.drain_quant_valid_reg&&DUT.output_valid_reg);
        begin_clip(0);assert_flushed();abort_checks=abort_checks+1;
        pulse_reset();checking=1;
        for(c=0;c<cases;c=c+1) begin
            local_outputs=0;local_window=0;begin_clip(samples[c]==0);
            for(n=0;n<samples[c];n=n+1) begin
                rc=$fscanf(pcm_fd,"%h",raw_pcm);if(rc!=1) $fatal(1,"PCM EOF");
                send_pcm(raw_pcm,n==samples[c]-1,n%79==5);
            end
            @(negedge clk);pcm_valid=0;pcm_last=0;
            wait(done_count==c+1);repeat(8) @(negedge clk);
            if(local_outputs!=frames[c]*512||local_window!=frames[c]*512) $fatal(1,"case count mismatch");
        end
        if(outputs!=total_frames*512||pre_reads!=outputs||window_reads!=outputs||window_writes!=outputs||
            first_stalls!=1||middle_stalls!=1||last_stalls!=1||stall_cycles==0) $fatal(1,"missing pipeline/stall coverage");
        report_fd=$fopen({run_dir,"/protocol.json"},"w");
        $fdisplay(report_fd,"{\"status\":\"PASS\",\"frames\":%0d,\"transactions\":%0d,\"mismatches\":0,\"cycles\":%0d,\"window_scan_min_cycles\":%0d,\"window_scan_max_cycles\":%0d,\"stall_cycles\":%0d,\"window_and_drain_reset_checks\":%0d,\"window_and_drain_clip_abort_checks\":%0d,\"first_middle_last_stalls\":true}",total_frames,outputs,cycles,scan_min,scan_max,stall_cycles,reset_checks,abort_checks);
        $fclose(report_fd);$fclose(cycle_fd);$display("FRONTEND_PIPELINE_PASS frames=%0d outputs=%0d",total_frames,outputs);$finish;
    end
endmodule
