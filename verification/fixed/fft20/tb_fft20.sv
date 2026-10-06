`timescale 1ns/1ps
module tb_fft20;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n=0, cfg_apply=0, in_valid=0, in_last=0;
    logic signed [19:0] in_re=0, in_im=0;
    wire cfg_ready,cfg_accept,cfg_reject,cfg_error,in_ready;
    wire [3:0] active_log2_n;
    wire out_valid,out_last,out_overflow,busy,done,overflow;
    wire signed [19:0] out_re,out_im;
    wire [9:0] out_index;
    wire input_frame_error,reorder_frame_error,bank_collision_error,config_consistency_error;
    logic [39:0] inputs [0:524287];
    logic [39:0] expected [0:524287];
    logic expected_overflow [0:1023];
    integer frames,received=0,cycles=0,overflows=0,fd,f,n,j,config_fd,scan_status;
    integer first_cycle=-1,last_cycle=-1;
    logic checking=0;
    fft20_stream_top U_DUT (
        .clk(clk),.rst_n(rst_n),.cfg_log2_n(4'd9),.cfg_apply(cfg_apply),
        .cfg_ready(cfg_ready),.cfg_accept(cfg_accept),.cfg_reject(cfg_reject),
        .cfg_error(cfg_error),.active_log2_n(active_log2_n),
        .in_valid(in_valid),.in_ready(in_ready),.in_re(in_re),.in_im(in_im),.in_last(in_last),
        .out_valid(out_valid),.out_re(out_re),.out_im(out_im),.out_index(out_index),
        .out_last(out_last),.out_overflow(out_overflow),.busy(busy),.done(done),.overflow(overflow),
        .input_frame_error(input_frame_error),.reorder_frame_error(reorder_frame_error),
        .bank_collision_error(bank_collision_error),.config_consistency_error(config_consistency_error)
    );
    always @(posedge clk) begin
        cycles=cycles+1;
        if (cycles>3000000) $fatal(1,"watchdog");
        if (rst_n && checking) begin
            if (cfg_error || input_frame_error || reorder_frame_error || bank_collision_error || config_consistency_error)
                $fatal(1,"Protocol error at cycle %0d: %b%b%b%b%b",cycles,cfg_error,input_frame_error,reorder_frame_error,bank_collision_error,config_consistency_error);
            if (out_valid) begin
                if (received>=frames*512) $fatal(1,"unexpected extra output");
                if ({out_re,out_im} !== expected[received])
                    $fatal(1,"Bits frame %0d bin %0d got %h expected %h",received/512,received%512,{out_re,out_im},expected[received]);
                if (out_index !== (received%512) || out_last !== ((received%512)==511))
                    $fatal(1,"Index/last mismatch at %0d",received);
                if (out_overflow !== expected_overflow[received/512])
                    $fatal(1,"Overflow mismatch frame %0d got %b expected %b",received/512,out_overflow,expected_overflow[received/512]);
                if (first_cycle<0) first_cycle=cycles;
                last_cycle=cycles;
                $fwrite(fd,"%0d,%0d,%0d,%0d,%0d,%0d\n",received/512,out_index,$signed(out_re),$signed(out_im),out_overflow,cycles);
                if (out_last && out_overflow) overflows=overflows+1;
                received=received+1;
            end
        end
    end
    task reset_configure;
        begin
            @(negedge clk); rst_n=0; in_valid=0; in_last=0; cfg_apply=0;
            repeat(5) @(negedge clk);
            rst_n=1;
            while(!cfg_ready) @(negedge clk);
            cfg_apply=1;
            @(negedge clk); cfg_apply=0;
            if (!cfg_accept) $fatal(1,"configuration not accepted");
        end
    endtask
    task send_sample(input integer address, input bit last);
        begin
            in_valid=1; in_re=inputs[address][39:20]; in_im=inputs[address][19:0]; in_last=last;
            @(posedge clk);
            while(!in_ready) @(posedge clk);
            @(negedge clk);
        end
    endtask
    initial begin
        config_fd=$fopen("frame_count.txt","r");
        scan_status=$fscanf(config_fd,"%d",frames);
        $fclose(config_fd);
        if(scan_status!=1 || frames<1 || frames>1024) $fatal(1,"invalid frame_count.txt");
        $readmemh("inputs.mem",inputs,0,frames*512-1);
        $readmemh("expected.mem",expected,0,frames*512-1);
        $readmemh("overflow.mem",expected_overflow,0,frames-1);
        fd=$fopen("fft20_outputs.csv","w");
        $fwrite(fd,"frame,bin,re,im,overflow,cycle\n");
        reset_configure();
        // Deliberately discard a partial frame. RAM contents are not reset;
        // token-valid state must prevent stale entries appearing afterwards.
        for(j=0;j<173;j=j+1) send_sample(j,0);
        reset_configure(); checking=1;
        for(f=0;f<frames;f=f+1) begin
            // Eight adjacent frames exercise continuous scheduling. Others
            // have frame-boundary gaps that force autonomous dummy drain.
            if ((f%9)==8) begin
                in_valid=0; in_last=0;
                repeat(5) @(negedge clk);
            end
            for(n=0;n<512;n=n+1) begin
                // Gaps inside a frame, never accidentally dropping a beat.
                if ((f%3)==1 && n>0 && (n%19)==7) begin
                    in_valid=0; in_last=0;
                    repeat(2) @(negedge clk);
                end
                send_sample(f*512+n,n==511);
            end
        end
        in_valid=0; in_last=0;
        while(received<frames*512) @(negedge clk);
        repeat(2000) @(negedge clk);
        if(busy) $fatal(1,"last frame did not drain");
        $fclose(fd);
        $display("FFT20_PASS frames=%0d bins=%0d overflow_frames=%0d first_cycle=%0d last_cycle=%0d reset_partial=173",frames,received,overflows,first_cycle,last_cycle);
        $finish;
    end
endmodule
