`timescale 1ns/1ps
// Native FP32 boundary is driven directly: transport protocol, not arithmetic.
module tb_fp32_core_adapter;
    reg clk=0; always #5 clk=~clk;
    reg rst_n=0, clip_start=0, abort_core=0, pcm_valid=0, pcm_last=0;
    reg signed [15:0] pcm=0;
    wire pcm_ready, output_valid, output_last, output_error, metadata_error, clip_done;
    reg output_ready=0;
    wire [63:0] output_data;
    wire [31:0] output_frame;
    wire [3:0] output_index;
    wire signed [7:0] output_bfp;
    wire [11:0] error_detail;
    wire core_rst_n, start_valid, sample_valid, end_valid, result_ready, done_ready;
    wire [15:0] sample_pcm;
    reg start_ready=0, sample_ready=0, end_ready=0, result_valid=0, result_last=0, done_valid=0;
    reg [31:0] result_data=0, result_frame=0, result_start=0;
    reg [3:0] result_index=0;
    reg [11:0] core_error=0;
    integer cycles=0, checks=0, reset_edges=0, high_edges=0, reset_periods=0, starts=0, samples=0, ends=0, dones=0, outputs=0;
    integer fd,k,old_samples,old_outputs;
    reg was_reset=1;
    string run_dir;

    fp32_core_adapter DUT (
        .clk(clk),.rst_n(rst_n),.i_clip_start(clip_start),.i_abort(abort_core),
        .i_pcmvalid(pcm_valid),.o_pcmready(pcm_ready),.i_pcm(pcm),.i_pcm_last(pcm_last),
        .o_outputvalid(output_valid),.i_outputready(output_ready),.o_data64(output_data),
        .o_frame32(output_frame),.o_index4(output_index),.o_bfp8(output_bfp),
        .o_last(output_last),.o_error(output_error),.o_metadata_error(metadata_error),
        .o_core_error_detail(error_detail),.o_clipdone(clip_done),.o_core_rst_n(core_rst_n),
        .o_start_valid(start_valid),.i_start_ready(start_ready),
        .o_sample_valid(sample_valid),.i_sample_ready(sample_ready),.o_sample_pcm(sample_pcm),
        .o_end_valid(end_valid),.i_end_ready(end_ready),.i_result_valid(result_valid),.o_result_ready(result_ready),
        .i_result_data(result_data),.i_result_index(result_index),.i_result_frame(result_frame),
        .i_result_start(result_start),.i_result_last(result_last),.i_done_valid(done_valid),
        .o_done_ready(done_ready),.i_core_error(core_error)
    );

    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>10000) $fatal(1,"adapter watchdog checks=%0d",checks);
        if(!core_rst_n) begin
            if(!was_reset && high_edges<4) $fatal(1,"reset release shorter than4 clocks: %0d",high_edges);
            high_edges=0;
            reset_edges=reset_edges+1;
            if(start_valid||sample_valid||pcm_ready||end_valid||output_valid||result_ready||done_ready)
                $fatal(1,"native stream live during reset");
        end else begin
            high_edges=high_edges+1;
            if(was_reset) begin
                if(reset_edges<16) $fatal(1,"reset shorter than16 clocks: %0d",reset_edges);
                reset_periods=reset_periods+1;reset_edges=0;
            end
            if(high_edges<=4 && (start_valid||sample_valid||pcm_ready||end_valid||output_valid||result_ready||done_ready))
                $fatal(1,"native handshake enabled during release guard");
        end
        was_reset=!core_rst_n;
        if(start_valid&&start_ready) starts=starts+1;
        if(sample_valid&&sample_ready) samples=samples+1;
        if(end_valid&&end_ready) ends=ends+1;
        if(clip_done) dones=dones+1;
        if(output_valid&&output_ready) outputs=outputs+1;
        if(output_valid && (output_data!=={32'd0,result_data} || output_bfp!==0 ||
                           output_frame!==result_frame || output_index!==result_index || output_last!==result_last))
            $fatal(1,"payload/metadata mapping changed raw FP32");
    end

    task request_start(input bit empty);
        begin
            @(negedge clk);clip_start=1;pcm_last=empty;
            @(negedge clk);clip_start=0;pcm_last=0;
        end
    endtask
    task accept_start;
        begin
            while(!start_valid) @(negedge clk);
            repeat(5) begin
                @(negedge clk);
                if(!start_valid||sample_valid||pcm_ready||end_valid) $fatal(1,"start not held before acceptance");
            end
            start_ready=1;@(negedge clk);start_ready=0;
        end
    endtask
    task accept_end;
        begin
            while(!end_valid) @(negedge clk);
            repeat(7) begin
                @(negedge clk);
                if(!end_valid||sample_valid||pcm_ready) $fatal(1,"EOF not held or PCM live after final sample");
            end
            end_ready=1;@(negedge clk);end_ready=0;
        end
    endtask
    task finish_clip;
        begin
            while(!done_ready) @(negedge clk);
            done_valid=1;@(negedge clk);done_valid=0;
            if(!clip_done) $fatal(1,"native done not translated");
            @(negedge clk);
            if(clip_done||sample_valid||pcm_ready||done_ready||output_valid) $fatal(1,"done pulse/idle behavior");
        end
    endtask
    task send_sample(input [15:0] value,input bit last,input integer stall);
        begin
            @(negedge clk);pcm=value;pcm_last=last;pcm_valid=1;sample_ready=0;
            repeat(stall) begin
                @(negedge clk);
                if(!sample_valid||sample_pcm!==value||pcm_ready||end_valid) $fatal(1,"final/sample stalled incorrectly");
            end
            sample_ready=1;@(negedge clk);sample_ready=0;pcm_valid=0;pcm_last=0;
        end
    endtask
    task emit_result(input [31:0] frame,input [31:0] start,input [3:0] index,input integer stall);
        begin
            @(negedge clk);result_frame=frame;result_start=start;result_index=index;
            result_data=32'hbf812345+{28'd0,index};result_last=(index==12);result_valid=1;output_ready=0;
            repeat(stall) begin
                @(negedge clk);
                if(!output_valid||result_ready||output_data!=={32'd0,result_data}||output_last!==(index==12))
                    $fatal(1,"output stall did not preserve native record");
            end
            output_ready=1;@(negedge clk);output_ready=0;result_valid=0;
        end
    endtask

    initial begin
        run_dir=".";if($value$plusargs("RUN=%s",run_dir)) begin end
        repeat(3) @(negedge clk);rst_n=1;
        // Empty START during initial reset: no sample transaction, held EOF.
        old_samples=samples;request_start(1);accept_start();accept_end();finish_clip();
        if(samples!=old_samples||output_error||error_detail) $fatal(1,"empty clip fabricated sample/error");
        checks=checks+1;

        // A short/tail clip still forwards its exact final sample before EOF.
        request_start(0);accept_start();old_samples=samples;
        send_sample(16'h8001,0,3);send_sample(16'h0001,0,0);send_sample(16'h7fff,1,11);
        accept_end();finish_clip();
        if(samples-old_samples!=3) $fatal(1,"tail samples dropped/duplicated");
        checks=checks+1;

        // Output can flow before EOF and during drain. Last remains held under stall.
        request_start(0);accept_start();old_outputs=outputs;
        for(k=0;k<12;k=k+1) emit_result(7,1120,4'(k),k%3);
        send_sample(16'h1234,1,2);accept_end();emit_result(7,1120,12,17);finish_clip();
        if(outputs-old_outputs!=13||output_error||metadata_error||error_detail) $fatal(1,"valid metadata/output sequence");
        checks=checks+1;

        // A bad start_sample is visible throughout stall, sticky after transfer;
        // no native error bit is appropriated to represent adapter metadata.
        request_start(0);accept_start();
        @(negedge clk);result_frame=7;result_start=1119;result_index=0;result_last=0;
        result_valid=1;output_ready=0;
        repeat(9) begin
            @(negedge clk);
            if(!output_valid||!metadata_error||!output_error||error_detail) $fatal(1,"bad start metadata hidden under stall");
        end
        output_ready=1;@(negedge clk);output_ready=0;result_valid=0;
        if(!metadata_error||!output_error||error_detail) $fatal(1,"metadata fault not sticky");
        // First native vector is exact: later encoded backend codes cannot OR
        // together into an invented third code. No output handshake is needed.
        core_error=12'h807;@(negedge clk);core_error=12'h409;
        @(negedge clk);core_error=0;@(negedge clk);
        if(error_detail!==12'h807||!output_error) $fatal(1,"native12 first-fault capture");
        checks=checks+1;

        // ABORT flushes a held last output; START during its reset is queued.
        result_frame=0;result_start=0;result_index=12;result_last=1;result_valid=1;output_ready=0;
        @(negedge clk);abort_core=1;
        @(negedge clk);abort_core=0;result_valid=0;
        if(output_valid||error_detail||metadata_error||output_error||clip_done) $fatal(1,"ABORT did not flush errors/output");
        repeat(3) @(negedge clk);request_start(1);accept_start();accept_end();finish_clip();
        if(error_detail||metadata_error||output_error) $fatal(1,"restart retains previous fault");
        checks=checks+1;

        // ABORT wins simultaneous START; no phantom start after reset completes.
        @(negedge clk);abort_core=1;clip_start=1;pcm_last=1;
        @(negedge clk);abort_core=0;clip_start=0;pcm_last=0;
        repeat(24) @(negedge clk);
        if(start_valid||sample_valid||end_valid||clip_done) $fatal(1,"ABORT priority lost");
        request_start(1);accept_start();accept_end();finish_clip();checks=checks+1;

        // Request a new START on the very first released high clock. It must
        // wait for all4 high clocks, then reset16 again before native START.
        @(negedge clk);abort_core=1;
        @(negedge clk);abort_core=0;
        while(!core_rst_n) @(negedge clk);
        clip_start=1;pcm_last=1;
        @(negedge clk);clip_start=0;pcm_last=0;
        if(!core_rst_n||start_valid||sample_valid) $fatal(1,"queued START shortened release");
        accept_start();accept_end();finish_clip();checks=checks+1;

        // ABORT during release cancels a pending START; the queued reset still
        // observes the4-high guard. An immediate later START during LOW works.
        request_start(1);
        while(!core_rst_n) @(negedge clk);
        abort_core=1;
        @(negedge clk);abort_core=0;
        if(!core_rst_n||start_valid) $fatal(1,"queued ABORT shortened release");
        while(core_rst_n) @(negedge clk);
        repeat(2) @(negedge clk);request_start(1);
        accept_start();accept_end();finish_clip();checks=checks+1;

        if(checks!=8||reset_periods<11||starts!=8||samples!=4||ends!=7||dones!=7||outputs!=14)
            $fatal(1,"coverage checks=%0d reset=%0d starts=%0d samples=%0d ends=%0d done=%0d outputs=%0d",checks,reset_periods,starts,samples,ends,dones,outputs);
        fd=$fopen({run_dir,"/fp32_adapter_unit_simulation.json"},"w");
        $fdisplay(fd,"{\"status\":\"PASS\",\"scope\":\"direct native boundary, no FP32 arithmetic\",\"checks\":%0d,\"cycles\":%0d,\"reset_periods\":%0d,\"minimum_reset_low_clocks\":16,\"minimum_reset_high_clocks\":4,\"queued_start_during_abort\":true,\"queued_commands_at_release_edge\":true,\"empty_tail\":true,\"held_last\":true,\"bad_start_under_stall\":true,\"native_error_width\":12,\"first_native_fault_exact\":true}",checks,cycles,reset_periods);
        $fclose(fd);$display("FP32_ADAPTER_UNIT_PASS checks=%0d",checks);$finish;
    end
endmodule
