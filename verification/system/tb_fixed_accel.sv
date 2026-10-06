`timescale 1ns/1ps
// AXI4-Lite bus-master integration test. Testbench tasks/loops are not RTL.
module tb_fixed_accel;
    reg clk=0; always #5 clk=~clk;
    reg rst_n=0;
    reg [15:0] awaddr=0,araddr=0;
    reg [2:0] awprot=0,arprot=0;
    reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
    reg [31:0] wdata=0; reg [3:0] wstrb=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp; wire [31:0] rdata;
    fixed_accel_top DUT(
        .s_axi_aclk(clk),.s_axi_aresetn(rst_n),
        .s_axi_awaddr(awaddr),.s_axi_awprot(awprot),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(arprot),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready));
    integer cycles=0,reads=0,writes=0,rstall=0,bstall=0,core_stall=0;
    integer cases,total_frames,pcm_fd,mfcc_fd,fd,rc,n,c,sent,received,frame_base=0,total_output=0;
    integer samples[0:31],frames[0:31];
    reg [7:0] shifts[0:1023];
    reg [15:0] pcm_word; reg [39:0] expected;
    reg [31:0] status_word,low_word,high_word,frame_word,meta_word,temp_word;
    reg [63:0] expected64;
    reg prev_rstall=0,prev_bstall=0,prev_corestall=0;
    reg [33:0] held_r; reg [1:0] held_b; reg [85:0] held_core;
    reg long_stall_done=0,finished_case;
    string run_dir;
    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>1000000+total_frames*100000) $fatal(1,"AXI integration watchdog c=%0d sent=%0d received=%0d",c,sent,received);
        if(!rst_n) begin prev_rstall=0;prev_bstall=0;prev_corestall=0;end
        else begin
            if(prev_rstall && (!rvalid || {rdata,rresp}!==held_r)) $fatal(1,"AXI R changed under stall");
            if(prev_bstall && (!bvalid || bresp!==held_b)) $fatal(1,"AXI B changed under stall");
            prev_rstall=rvalid&&!rready;prev_bstall=bvalid&&!bready;
            held_r={rdata,rresp};held_b=bresp;
            if(prev_rstall) rstall=rstall+1;
            if(prev_bstall) bstall=bstall+1;
            // Core reset/clip-start cancels pending work by contract.
            if(!DUT.U_CORE.rst_n || DUT.U_CORE.i_clip_start) prev_corestall=0;
            else begin
                if(prev_corestall && (!DUT.U_CORE.o_valid || held_core!=={DUT.U_CORE.o_mfcc,DUT.U_CORE.o_frame,DUT.U_CORE.o_index,DUT.U_CORE.o_bfp_s,DUT.U_CORE.o_last,DUT.U_CORE.o_error}))
                    $fatal(1,"core payload changed under transport backpressure");
                prev_corestall=DUT.U_CORE.o_valid&&!DUT.U_CORE.i_ready;
                held_core={DUT.U_CORE.o_mfcc,DUT.U_CORE.o_frame,DUT.U_CORE.o_index,DUT.U_CORE.o_bfp_s,DUT.U_CORE.o_last,DUT.U_CORE.o_error};
                if(prev_corestall) core_stall=core_stall+1;
            end
        end
    end
    task reset_bus;
        begin
            @(negedge clk);rst_n=0;awvalid=0;wvalid=0;bready=0;arvalid=0;rready=0;
            repeat(8) @(negedge clk);
            rst_n=1;repeat(8) @(negedge clk);
        end
    endtask
    task put_aw(input [15:0] addr);
        begin
            @(negedge clk);awaddr=addr;awvalid=1;
            while(!awready) @(negedge clk);
            @(posedge clk);@(negedge clk);awvalid=0;
        end
    endtask
    task put_w(input [31:0] value,input [3:0] strobes);
        begin
            @(negedge clk);wdata=value;wstrb=strobes;wvalid=1;
            while(!wready) @(negedge clk);
            @(posedge clk);@(negedge clk);wvalid=0;
        end
    endtask
    task bus_write(input [15:0] addr,input [31:0] value,input [3:0] strobes,input [1:0] response);
        integer order;
        begin
            order=writes%3;
            if(order==0) begin put_aw(addr);repeat(3) @(negedge clk);put_w(value,strobes);end
            else if(order==1) begin put_w(value,strobes);repeat(5) @(negedge clk);put_aw(addr);end
            else fork put_aw(addr);put_w(value,strobes);join
            while(!bvalid) @(negedge clk);
            if(bresp!==response) $fatal(1,"BRESP address=%h value=%h got=%b wanted=%b",addr,value,bresp,response);
            if(writes%17==0) repeat(9) @(negedge clk);
            bready=1;@(posedge clk);@(negedge clk);bready=0;writes=writes+1;
        end
    endtask
    task bus_read(input [15:0] addr,output [31:0] value,input [1:0] response);
        begin
            @(negedge clk);araddr=addr;arvalid=1;
            while(!arready) @(negedge clk);
            @(posedge clk);@(negedge clk);arvalid=0;
            while(!rvalid) @(negedge clk);
            value=rdata;
            if(rresp!==response) $fatal(1,"RRESP address=%h got=%b wanted=%b",addr,rresp,response);
            if(reads%23==0) repeat(8) @(negedge clk);
            rready=1;@(posedge clk);@(negedge clk);rready=0;reads=reads+1;
        end
    endtask
    task feed_constant(input integer count);
        integer k;reg [31:0] st;
        begin
            for(k=0;k<count;k=k+1) begin
                bus_read(16'h000c,st,2'b00);
                while(!st[3]) bus_read(16'h000c,st,2'b00);
                bus_write(16'h0014,32'h00001234,4'hf,2'b00);
            end
        end
    endtask
    initial begin
        if(!$value$plusargs("RUN=%s",run_dir)) $fatal(1,"RUN missing");
        fd=$fopen({run_dir,"/cases.txt"},"r");rc=$fscanf(fd,"%d %d",cases,total_frames);
        for(n=0;n<cases;n=n+1) rc=$fscanf(fd,"%d %d",samples[n],frames[n]);$fclose(fd);
        pcm_fd=$fopen({run_dir,"/pcm.mem"},"r");mfcc_fd=$fopen({run_dir,"/mfcc.mem"},"r");
        $readmemh({run_dir,"/shift.mem"},shifts,0,total_frames-1);
        if(!pcm_fd||!mfcc_fd) $fatal(1,"vector file missing");
        reset_bus();
        bus_read(16'h0000,temp_word,2'b00);if(temp_word!==32'h4d464343) $fatal(1,"ID");
        bus_read(16'h0040,temp_word,2'b00);if(temp_word!==1) $fatal(1,"CORE_ID");
        bus_read(16'h0044,temp_word,2'b00);if(temp_word!==32'h00011828) $fatal(1,"FORMAT");
        bus_read(16'h0060,temp_word,2'b00);if(temp_word!==32'h283fff8a) $fatal(1,"contract tag");
        // Illegal bus access cannot mutate SAMPLE_COUNT; error is sticky.
        bus_write(16'h0010,32'd999,4'h3,2'b10);
        bus_write(16'h0011,32'd888,4'hf,2'b10);
        bus_write(16'h0000,32'd1,4'hf,2'b10);
        bus_read(16'h0064,temp_word,2'b10);if(temp_word!==0) $fatal(1,"invalid read data");
        bus_read(16'h0018,temp_word,2'b10);
        bus_read(16'h0010,temp_word,2'b00);if(temp_word!==0) $fatal(1,"bad access mutated config");
        bus_read(16'h002c,temp_word,2'b00);if(!temp_word[0]) $fatal(1,"missing access error");
        bus_write(16'h0008,32'd4,4'hf,2'b00);
        bus_write(16'h0014,32'd0,4'hf,2'b10);
        bus_write(16'h0028,32'd1,4'hf,2'b10);
        bus_write(16'h0010,32'd262145,4'hf,2'b10);
        bus_write(16'h0008,32'd3,4'hf,2'b10);
        bus_read(16'h002c,temp_word,2'b00);if(temp_word[2:1]!==2'b11) $fatal(1,"missing command/input error");
        bus_write(16'h0008,32'd2,4'hf,2'b00);
        // Reset at independently accepted AW and W must discard each orphan.
        put_aw(16'h0010);reset_bus();
        bus_read(16'h0010,temp_word,2'b00);if(temp_word!==0 || bvalid) $fatal(1,"AW reset");
        put_w(32'd99,4'hf);reset_bus();
        bus_read(16'h0010,temp_word,2'b00);if(temp_word!==0 || bvalid) $fatal(1,"W reset");
        // Busy misuse, abort partial PCM, then reset with actual FFT in flight.
        bus_write(16'h0010,32'd2000,4'hf,2'b00);bus_write(16'h0008,32'd1,4'hf,2'b00);
        bus_write(16'h0008,32'd1,4'hf,2'b10);bus_write(16'h0010,32'd512,4'hf,2'b10);
        bus_write(16'h0008,32'd4,4'hf,2'b10);feed_constant(173);
        bus_write(16'h0008,32'd2,4'hf,2'b00);
        bus_read(16'h000c,temp_word,2'b00);if(temp_word!==0) $fatal(1,"abort status");
        bus_read(16'h0030,temp_word,2'b00);if(temp_word!==0) $fatal(1,"abort counter");
        bus_write(16'h0010,32'd512,4'hf,2'b00);bus_write(16'h0008,32'd1,4'hf,2'b00);feed_constant(512);
        wait(DUT.U_CORE.o_fft_valid);reset_bus();
        $display("SYSTEM_BUS_STRESS_PASS cycles=%0d",cycles);
        for(c=0;c<cases;c=c+1) begin
            sent=0;received=0;finished_case=0;
            bus_write(16'h0010,32'(samples[c]),4'hf,2'b00);
            bus_write(16'h0008,32'd1,4'hf,2'b00);
            while(!finished_case) begin
                bus_read(16'h000c,status_word,2'b00);
                if(status_word[2]) begin bus_read(16'h002c,temp_word,2'b00);$fatal(1,"case=%0d error=%h sent=%0d received=%0d",c,temp_word,sent,received);end
                if(status_word[4]) begin
                    if(!long_stall_done) begin repeat(5000) @(negedge clk);long_stall_done=1;end
                    bus_read(16'h0018,low_word,2'b00);bus_read(16'h001c,high_word,2'b00);
                    bus_read(16'h0020,frame_word,2'b00);bus_read(16'h0024,meta_word,2'b00);
                    if(received>=frames[c]*13) $fatal(1,"extra output");
                    rc=$fscanf(mfcc_fd,"%h",expected);if(rc!=1) $fatal(1,"MFCC EOF");
                    expected64={{24{expected[39]}},expected};
                    if({high_word,low_word}!==expected64 || frame_word!==32'(received/13) || meta_word[3:0]!==4'(received%13) || meta_word[15:8]!==shifts[frame_base+received/13] || meta_word[16]!==(received%13==12) || meta_word[31:17]!==0 || meta_word[7:4]!==0)
                        $fatal(1,"result mismatch case=%0d item=%0d actual=%h expected=%h frame=%0d meta=%h",c,received,{high_word,low_word},expected64,frame_word,meta_word);
                    // A second read must return the same record until explicit POP.
                    if(received==0) begin bus_read(16'h0018,temp_word,2'b00);if(temp_word!==low_word) $fatal(1,"read popped output");end
                    bus_write(16'h0028,32'd1,4'hf,2'b00);received=received+1;total_output=total_output+1;
                end
                if(status_word[3] && sent<samples[c]) begin
                    rc=$fscanf(pcm_fd,"%h",pcm_word);if(rc!=1) $fatal(1,"PCM EOF");
                    if(sent%79==5) repeat(3) @(negedge clk);
                    bus_write(16'h0014,{16'd0,pcm_word},4'hf,2'b00);sent=sent+1;
                end
                if(status_word[1] && received==frames[c]*13) finished_case=1;
            end
            if(sent!=samples[c]) $fatal(1,"early DONE");
            bus_read(16'h000c,temp_word,2'b00);if(temp_word!==2) $fatal(1,"final status=%h",temp_word);
            bus_read(16'h0030,temp_word,2'b00);if(temp_word!==32'(samples[c])) $fatal(1,"written count");
            bus_read(16'h0034,temp_word,2'b00);if(temp_word!==32'(samples[c])) $fatal(1,"consumed count");
            bus_read(16'h0038,temp_word,2'b00);if(temp_word!==32'(received)) $fatal(1,"captured count");
            bus_read(16'h003c,temp_word,2'b00);if(temp_word!==32'(received)) $fatal(1,"popped count");
            frame_base=frame_base+frames[c];
            $display("SYSTEM_CASE_PASS case=%0d samples=%0d frames=%0d output=%0d cycles=%0d",c,sent,frames[c],received,cycles);
        end
        if(frame_base!=total_frames || total_output!=total_frames*13 || rstall==0 || bstall==0 || core_stall==0) $fatal(1,"coverage counts");
        fd=$fopen({run_dir,"/simulation.json"},"w");
        $fdisplay(fd,"{\"status\":\"PASS\",\"cases\":%0d,\"frames\":%0d,\"mfcc_records\":%0d,\"mismatches\":0,\"cycles\":%0d,\"AXI_reads\":%0d,\"AXI_writes\":%0d,\"R_stall_cycles\":%0d,\"B_stall_cycles\":%0d,\"core_stall_cycles\":%0d,\"long_output_stall\":5000,\"reset_orphan_AW_W\":true,\"abort_partial_PCM\":173,\"reset_FFT_inflight\":true}",cases,total_frames,total_output,cycles,reads,writes,rstall,bstall,core_stall);
        $fclose(fd);$display("SYSTEM_INTEGRATION_PASS");$finish;
    end
endmodule
