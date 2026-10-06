`timescale 1ns/1ps
module tb_fixed_spectral_tail;
    logic clk=0;
    always #5 clk=~clk;
    logic rst_n=0, frame_start=0, frame_ready;
    logic [31:0] frame_id=0;
    logic signed [7:0] bfp_s=0;
    logic fft_valid=0;
    logic signed [19:0] fft_re=0, fft_im=0;
    logic [8:0] fft_bin=0;
    logic fft_last=0, fft_overflow=0;
    logic valid, ready=0;
    logic [59:0] mel;
    logic [4:0] band;
    logic [31:0] out_frame;
    logic signed [7:0] out_bfp;
    logic last, overflow, protocol_error, power_valid;
    logic [39:0] power;
    logic [8:0] power_bin;
    logic [39:0] inputs [0:524287];
    logic [39:0] powers [0:263167];
    logic [59:0] mels [0:26623];
    logic [7:0] shifts [0:1023];
    logic errors [0:1023];
    string root;
    integer frames, f, k, cycles=0, outputs=0, power_outputs=0, stalls=0;
    integer check_count=0, last_frame_cycle=0, max_frame_cycles=0, start_cycle=0;
    integer expected_frame=0, expected_band=0, expected_power_frame=0, expected_power_bin=0;
    integer report_fd, long_stall_until=0;
    integer mel_requests=0,previous_request_cycle=0,previous_request_band=-1,previous_request_frame=-1;
    integer request_ii_min=2147483647,request_ii_max=0;
    bit test_active=0, was_stalled=0;
    logic [107:0] held;
    fixed_spectral_tail U_DUT (
        .clk(clk), .rst_n(rst_n), .i_frame_start(frame_start), .o_frame_ready(frame_ready),
        .i_frame_id(frame_id), .i_bfp_s(bfp_s), .i_fft_valid(fft_valid),
        .i_fft_re(fft_re), .i_fft_im(fft_im), .i_fft_bin(fft_bin),
        .i_fft_last(fft_last), .i_fft_overflow(fft_overflow),
        .o_valid(valid), .i_ready(ready), .o_mel(mel), .o_band(band),
        .o_frame_id(out_frame), .o_bfp_s(out_bfp), .o_last(last),
        .o_overflow(overflow), .o_protocol_error(protocol_error),
        .o_power_valid(power_valid), .o_power(power), .o_power_bin(power_bin)
    );
    always @(negedge clk) begin
        if (test_active) ready = (cycles >= long_stall_until) && (cycles % 17 >= 6);
    end
    always @(posedge clk) begin
        cycles=cycles+1;
        if (cycles > 22000000) $fatal(1,"timeout");
        if (rst_n && test_active) begin
            if (U_DUT.rom_en) begin
                mel_requests=mel_requests+1;
                if(previous_request_band==band && previous_request_frame==out_frame) begin
                    if(cycles-previous_request_cycle<request_ii_min) request_ii_min=cycles-previous_request_cycle;
                    if(cycles-previous_request_cycle>request_ii_max) request_ii_max=cycles-previous_request_cycle;
                end
                previous_request_cycle=cycles;previous_request_band=band;previous_request_frame=out_frame;
            end
            if (was_stalled && (!valid || {mel,band,out_frame,out_bfp,last,overflow,protocol_error} !== held))
                $fatal(1,"stall stability failed");
            was_stalled=valid&&!ready;
            if (was_stalled) begin
                held={mel,band,out_frame,out_bfp,last,overflow,protocol_error};
                stalls=stalls+1;
            end
            if (power_valid) begin
                if (power !== powers[expected_power_frame*257+expected_power_bin] ||
                    power_bin !== expected_power_bin || out_frame !== expected_power_frame ||
                    out_bfp !== shifts[expected_power_frame])
                    $fatal(1,"Power mismatch frame=%0d bin=%0d got=%h expected=%h",expected_power_frame,expected_power_bin,power,powers[expected_power_frame*257+expected_power_bin]);
                power_outputs=power_outputs+1;
                if (expected_power_bin==256) begin expected_power_bin=0; expected_power_frame=expected_power_frame+1; end
                else expected_power_bin=expected_power_bin+1;
            end
            if (valid&&ready) begin
                if (mel !== mels[expected_frame*26+expected_band] || band !== expected_band ||
                    out_frame !== expected_frame || out_bfp !== shifts[expected_frame] ||
                    last !== (expected_band==25) || overflow !== errors[expected_frame] || protocol_error)
                    $fatal(1,"Mel mismatch frame=%0d band=%0d got=%h exp=%h err=%b/%b",expected_frame,expected_band,mel,mels[expected_frame*26+expected_band],overflow,protocol_error);
                outputs=outputs+1;
                if (expected_band==25) begin
                    expected_band=0; expected_frame=expected_frame+1;
                    if (cycles-start_cycle>max_frame_cycles) max_frame_cycles=cycles-start_cycle;
                end else expected_band=expected_band+1;
            end
        end else was_stalled=0;
    end
    task reserve(input integer id);
        begin
            while (!frame_ready) @(negedge clk);
            frame_start=1; frame_id=id; bfp_s=shifts[id];
            @(negedge clk); frame_start=0;
            start_cycle=cycles;
        end
    endtask
    task send_frame(input integer id, input bit gaps);
        begin
            for (integer b=0;b<512;b=b+1) begin
                if (gaps && (b%37==13)) begin fft_valid=0; repeat(3) @(negedge clk); end
                fft_valid=1; fft_bin=b; fft_last=(b==511);
                // Metadata is a reservation property, not a live sideband.
                if (b==1) begin frame_id=32'hdeadbeef; bfp_s=-8'sd2; end
                fft_re=inputs[id*512+b][39:20]; fft_im=inputs[id*512+b][19:0];
                fft_overflow=errors[id] && b==511;
                @(negedge clk);
            end
            fft_valid=0; fft_last=0; fft_overflow=0;
        end
    endtask
    task reset_dut;
        begin
            rst_n=0; frame_start=0; fft_valid=0; ready=0;
            repeat(3) @(negedge clk);
            if (valid || power_valid) $fatal(1,"reset valid failed");
            rst_n=1; @(negedge clk);
            if (!frame_ready || protocol_error || overflow) $fatal(1,"reset recovery failed");
        end
    endtask
    initial begin
        if (!$value$plusargs("RUN=%s",root)) $fatal(1,"RUN required");
        if (!$value$plusargs("FRAMES=%d",frames)) $fatal(1,"FRAMES required");
        $readmemh({root,"/fft.mem"},inputs,0,frames*512-1);
        $readmemh({root,"/power.mem"},powers,0,frames*257-1);
        $readmemh({root,"/mel_expected.mem"},mels,0,frames*26-1);
        $readmemh({root,"/shift.mem"},shifts,0,frames-1);
        $readmemh({root,"/overflow.mem"},errors,0,frames-1);
        reset_dut();
        reserve(0);
        for (k=0;k<13;k=k+1) begin
            fft_valid=1; fft_bin=k; fft_re=1; @(negedge clk);
        end
        reset_dut(); check_count=check_count+1;
        reserve(0); frame_start=1; @(negedge clk); frame_start=0;
        if (!protocol_error || frame_ready) $fatal(1,"busy reservation must flag error without releasing credit");
        reset_dut(); check_count=check_count+1;
        reserve(0); send_frame(0,0);
        wait(U_DUT.rom_en); @(negedge clk);
        reset_dut(); check_count=check_count+1;
        reserve(0); send_frame(0,0);
        while (!valid) @(negedge clk);
        repeat(11) @(negedge clk);
        reset_dut(); check_count=check_count+1;
        reserve(0); fft_valid=1; fft_bin=3; fft_last=0; @(negedge clk);
        fft_bin=511; fft_last=1; @(negedge clk); fft_valid=0; fft_last=0;
        repeat(3) @(negedge clk); // Drain both Power pipeline stages.
        if (!protocol_error || !frame_ready || valid) $fatal(1,"malformed frame recovery failed");
        check_count=check_count+1;
        reserve(0);
        for (k=0;k<13;k=k+1) begin
            fft_valid=1; fft_bin=k; fft_re=17; fft_im=-19; fft_last=(k==12);
            @(negedge clk);
        end
        fft_valid=0; fft_last=0;
        repeat(3) @(negedge clk);
        if (!protocol_error || !frame_ready || valid) $fatal(1,"early-last pipeline drain failed");
        check_count=check_count+1;
        test_active=1;
        for (f=0;f<frames;f=f+1) begin
            if (f==0) long_stall_until=cycles+7000;
            reserve(f); send_frame(f,f%2);
        end
        while (outputs<frames*26) @(negedge clk);
        repeat(20) @(negedge clk);
        if (outputs != frames*26 || power_outputs != frames*257 || stalls==0 || !frame_ready)
            $fatal(1,"counts/drain/stall failed");
        report_fd=$fopen({root,"/protocol.json"},"w");
        $fwrite(report_fd,"{\"status\":\"passed\",\"frames\":%0d,\"mel_transactions\":%0d,\"power_transactions\":%0d,\"stall_checks\":%0d,\"reset_abort_and_malformed_checks\":%0d,\"max_frame_cycles_with_stalls\":%0d,\"mel_requests\":%0d,\"term_ii_min\":%0d,\"term_ii_max\":%0d}\n",frames,outputs,power_outputs,stalls,check_count,max_frame_cycles,mel_requests,request_ii_min,request_ii_max);
        $fclose(report_fd);
        $display("PASS frames=%0d mel=%0d power=%0d stalls=%0d",frames,outputs,power_outputs,stalls);
        $finish;
    end
endmodule
