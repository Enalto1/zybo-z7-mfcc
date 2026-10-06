`timescale 1ns/1ps
module tb_fixed_spectral;
    logic clk=0; always #5 clk=~clk;
    logic rst_n=0,i_valid=0,i_last=0,i_ready=0;
    logic signed [15:0] i_re=0,i_im=0;
    logic [31:0] i_frame_id=0;
    logic signed [7:0] i_bfp_s=0;
    wire o_ready,o_valid,o_last,o_overflow,o_protocol_error,o_busy;
    wire [59:0] o_mel;
    wire [4:0] o_band;
    wire [31:0] o_frame_id;
    wire signed [7:0] o_bfp_s;
    wire o_fft_valid,o_fft_last;
    wire signed [19:0] o_fft_re,o_fft_im;
    wire [8:0] o_fft_bin;
    logic [31:0] inputs[0:524287];
    logic [39:0] expected_fft[0:524287];
    logic [39:0] expected_power[0:263167];
    logic [59:0] expected_mel[0:26623];
    logic [7:0] expected_bfp[0:1023];
    logic expected_overflow[0:1023];
    integer frames,cfgfd,scan,fft_count=0,power_count=0,mel_count=0;
    integer cycles=0,stalls=0,backpressure=0,fftfd,melfd,f,n,j;
    integer first_output_cycle=-1,last_output_cycle=-1;
    logic checking=0,stalled=0,auto_ready=0;
    logic [107:0] saved_output;
    fixed_spectral_top U_DUT(
        .clk(clk),.rst_n(rst_n),.i_valid(i_valid),.o_ready(o_ready),
        .i_re(i_re),.i_im(i_im),.i_last(i_last),.i_frame_id(i_frame_id),.i_bfp_s(i_bfp_s),
        .o_valid(o_valid),.i_ready(i_ready),.o_mel(o_mel),.o_band(o_band),
        .o_frame_id(o_frame_id),.o_bfp_s(o_bfp_s),.o_last(o_last),
        .o_overflow(o_overflow),.o_protocol_error(o_protocol_error),.o_busy(o_busy),
        .o_fft_valid(o_fft_valid),.o_fft_re(o_fft_re),.o_fft_im(o_fft_im),
        .o_fft_bin(o_fft_bin),.o_fft_last(o_fft_last)
    );
    always @(negedge clk) begin
        if(auto_ready) i_ready = (cycles%23)<13 && !((cycles%200003)<1000);
    end
    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>30000000) $fatal(1,"watchdog");
        if(checking && rst_n) begin
            if(o_protocol_error) $fatal(1,"protocol error cycle %0d",cycles);
            if(i_valid && !o_ready) backpressure=backpressure+1;
            if(stalled) begin
                if(!o_valid || saved_output !== {o_mel,o_band,o_frame_id,o_bfp_s,o_last,o_overflow,o_protocol_error})
                    $fatal(1,"stalled output changed");
            end
            stalled=o_valid && !i_ready;
            saved_output={o_mel,o_band,o_frame_id,o_bfp_s,o_last,o_overflow,o_protocol_error};
            if(stalled) stalls=stalls+1;
            if(o_fft_valid) begin
                if(fft_count>=frames*512 || {o_fft_re,o_fft_im} !== expected_fft[fft_count] ||
                   o_fft_bin !== (fft_count%512) || o_fft_last !== ((fft_count%512)==511))
                    $fatal(1,"FFT mismatch frame%0d bin%0d got%h expected%h",fft_count/512,fft_count%512,{o_fft_re,o_fft_im},expected_fft[fft_count]);
                $fwrite(fftfd,"%0d,%0d,%0d,%0d\n",fft_count/512,o_fft_bin,$signed(o_fft_re),$signed(o_fft_im));
                fft_count=fft_count+1;
            end
            // Tail unit outputs are inspected hierarchically only by this TB;
            // the synthesized public top interface remains unchanged.
            if(U_DUT.U_TAIL.o_power_valid) begin
                if(power_count>=frames*257 || U_DUT.U_TAIL.o_power !== expected_power[power_count] ||
                   U_DUT.U_TAIL.o_power_bin !== (power_count%257)) $fatal(1,"Power mismatch %0d",power_count);
                power_count=power_count+1;
            end
            if(o_valid && i_ready) begin
                if(mel_count>=frames*26 || o_mel !== expected_mel[mel_count] || o_band !== (mel_count%26) ||
                   o_last !== ((mel_count%26)==25) || o_frame_id !== (1000+7*(mel_count/26)) ||
                   o_bfp_s !== expected_bfp[mel_count/26] || o_overflow !== expected_overflow[mel_count/26])
                    $fatal(1,"Mel/meta mismatch frame%0d band%0d got%h expected%h id%0d s%0d ov%b",mel_count/26,mel_count%26,o_mel,expected_mel[mel_count],o_frame_id,$signed(o_bfp_s),o_overflow);
                if(first_output_cycle<0) first_output_cycle=cycles;
                last_output_cycle=cycles;
                $fwrite(melfd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",mel_count/26,o_band,o_mel,o_frame_id,$signed(o_bfp_s),o_overflow,cycles);
                mel_count=mel_count+1;
            end
        end else stalled=0;
    end
    task reset_dut;
        begin
            @(negedge clk);rst_n=0;i_valid=0;i_last=0;auto_ready=0;i_ready=0;
            repeat(5) @(negedge clk);
            if(o_valid || o_fft_valid) $fatal(1,"reset valid not cleared");
            rst_n=1;
            while(!o_ready) @(negedge clk);
        end
    endtask
    task sample(input integer address,input integer frame,input bit last);
        begin
            i_re=inputs[address][31:16];i_im=inputs[address][15:0];
            i_frame_id=1000+7*frame;i_bfp_s=expected_bfp[frame];i_valid=1;i_last=last;
            @(posedge clk);while(!o_ready) @(posedge clk);
            @(negedge clk);
        end
    endtask
    initial begin
        cfgfd=$fopen("frame_count.txt","r");scan=$fscanf(cfgfd,"%d",frames);$fclose(cfgfd);
        if(scan!=1 || frames<1 || frames>1024) $fatal(1,"invalid frame count");
        $readmemh("inputs.mem",inputs,0,frames*512-1);
        $readmemh("fft_expected.mem",expected_fft,0,frames*512-1);
        $readmemh("power_expected.mem",expected_power,0,frames*257-1);
        $readmemh("mel_expected.mem",expected_mel,0,frames*26-1);
        $readmemh("bfp.mem",expected_bfp,0,frames-1);
        $readmemh("overflow.mem",expected_overflow,0,frames-1);
        fftfd=$fopen("fft_outputs.csv","w");$fwrite(fftfd,"frame,bin,re,im\n");
        melfd=$fopen("mel_outputs.csv","w");$fwrite(melfd,"frame,band,mel,frame_id,bfp_s,overflow,cycle\n");
        reset_dut();
        for(j=0;j<173;j=j+1) sample(j,0,0);
        reset_dut();
        for(j=0;j<512;j=j+1) sample(j,0,j==511);
        i_valid=0;while(!o_fft_valid) @(negedge clk);
        reset_dut();
        for(j=0;j<512;j=j+1) sample(j,0,j==511);
        i_valid=0;while(!o_valid) @(negedge clk);
        repeat(53) @(negedge clk);
        reset_dut();
        // Wrong input last is flagged. Reset recovers without completing it.
        for(j=0;j<17;j=j+1) sample(j,0,j==11);
        if(!o_protocol_error) $fatal(1,"malformed last not detected");
        reset_dut();checking=1;auto_ready=1;
        for(f=0;f<frames;f=f+1) begin
            for(n=0;n<512;n=n+1) begin
                if((f%3)==1 && n>0 && (n%19)==7) begin
                    i_valid=0;i_last=0;repeat(2) @(negedge clk);
                end
                sample(f*512+n,f,n==511);
            end
        end
        i_valid=0;i_last=0;
        while(mel_count<frames*26) @(negedge clk);
        repeat(1200) @(negedge clk);
        if(fft_count!=frames*512 || power_count!=frames*257 || o_busy || stalls==0 || backpressure==0)
            $fatal(1,"count/drain/stall coverage failure");
        $fclose(fftfd);$fclose(melfd);
        $display("SPECTRAL_PASS frames=%0d fft_bins=%0d powers=%0d mels=%0d stalls=%0d input_backpressure=%0d first_cycle=%0d last_cycle=%0d",frames,fft_count,power_count,mel_count,stalls,backpressure,first_output_cycle,last_output_cycle);
        $finish;
    end
endmodule
