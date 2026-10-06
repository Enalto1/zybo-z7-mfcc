`timescale 1ns/1ps
// AXI4-Lite bus-master integration test. Testbench tasks/loops are not RTL.
module tb_fp32_axi_vendor;
    reg clk=0; always #5 clk=~clk;
    reg rst_n=0;
    reg [15:0] awaddr=0,araddr=0;
    reg [2:0] awprot=0,arprot=0;
    reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
    reg [31:0] wdata=0; reg [3:0] wstrb=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp; wire [31:0] rdata;
    fp32_accel_top DUT(
        .s_axi_aclk(clk),.s_axi_aresetn(rst_n),
        .s_axi_awaddr(awaddr),.s_axi_awprot(awprot),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(arprot),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready));
    integer cycles=0,reads=0,writes=0,rstall=0,bstall=0,core_stall=0;
    integer pcm_fd,mfcc_fd,fd,rc,n,c,sent=0,received=0,total_output=0;
    integer samples=672,frames=2;
    integer native_starts=0,native_ends=0,native_dones=0;
    reg [15:0] pcm[0:671]; reg [31:0] expected_mfcc[0:25];
    reg [31:0] status_word,low_word,high_word,frame_word,meta_word,temp_word;
    reg prev_rstall=0,prev_bstall=0,prev_corestall=0;
    reg [33:0] held_r; reg [1:0] held_b; reg [100:0] held_core;
    reg long_stall_done=0,finished_case;
    reg reset_previous=0;
    integer reset_span=0,reset_enabled_span=0,reset_transitions=0;
    string run_dir;
    always @(posedge clk) begin
        cycles=cycles+1;
        // Record the exact external reset and FFT CE history independently of
        // vendor internal warnings. All widths are sampled rising clock edges.
        if(DUT.core_rst_n!==reset_previous) begin
            $display("FP32_RESET_EDGE time=%0t cycle=%0d previous=%b width=%0d fft_enabled_edges=%0d next=%b start=%b abort=%b",$time,cycles,reset_previous,reset_span,reset_enabled_span,DUT.core_rst_n,DUT.clip_start,DUT.abort_core);
            if(reset_transitions>0 && ((!reset_previous && reset_span<16) || (reset_previous && reset_span<4)))
                $fatal(1,"Native reset violates adapter low16/high4 sampled clock guard");
            reset_previous=DUT.core_rst_n;reset_span=0;reset_enabled_span=0;reset_transitions=reset_transitions+1;
        end
        reset_span=reset_span+1;
        if(DUT.U_CORE.U_BACKEND.fft_aclken) reset_enabled_span=reset_enabled_span+1;
        if(DUT.clip_start || DUT.abort_core)
            $display("FP32_RESET_COMMAND time=%0t cycle=%0d start=%b abort=%b reset_n=%b fft_ce=%b",$time,cycles,DUT.clip_start,DUT.abort_core,DUT.core_rst_n,DUT.U_CORE.U_BACKEND.fft_aclken);
        if(cycles>2000000) $fatal(1,"AXI integration watchdog c=%0d sent=%0d received=%0d",c,sent,received);
        if(!rst_n) begin prev_rstall=0;prev_bstall=0;prev_corestall=0;end
        else begin
            if(prev_rstall && (!rvalid || {rdata,rresp}!==held_r)) $fatal(1,"AXI R changed under stall");
            if(prev_bstall && (!bvalid || bresp!==held_b)) $fatal(1,"AXI B changed under stall");
            prev_rstall=rvalid&&!rready;prev_bstall=bvalid&&!bready;
            held_r={rdata,rresp};held_b=bresp;
            if(prev_rstall) rstall=rstall+1;
            if(prev_bstall) bstall=bstall+1;
            if(!DUT.core_rst_n) prev_corestall=0;
            else begin
                if(prev_corestall && (!DUT.result_valid || held_core!=={DUT.result_data,DUT.result_index,DUT.result_frame,DUT.result_start,DUT.result_last}))
                    $fatal(1,"native FP32 output changed under transport backpressure");
                prev_corestall=DUT.result_valid&&!DUT.result_ready;
                held_core={DUT.result_data,DUT.result_index,DUT.result_frame,DUT.result_start,DUT.result_last};
                if(prev_corestall) core_stall=core_stall+1;
                if(DUT.start_valid&&DUT.start_ready) native_starts=native_starts+1;
                if(DUT.end_valid&&DUT.end_ready) native_ends=native_ends+1;
                if(DUT.done_valid&&DUT.done_ready) native_dones=native_dones+1;
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
        $readmemh({run_dir,"/pcm.mem"},pcm,0,671);
        $readmemh({run_dir,"/mfcc.mem"},expected_mfcc,0,25);
        reset_bus();
        bus_read(16'h0000,temp_word,2'b00);if(temp_word!==32'h4d464343) $fatal(1,"ID");
        bus_read(16'h0004,temp_word,2'b00);if(temp_word!==32'h00010001) $fatal(1,"VERSION");
        bus_read(16'h0040,temp_word,2'b00);if(temp_word!==32'd2) $fatal(1,"CORE_ID");
        bus_read(16'h0044,temp_word,2'b00);if(temp_word!==32'h00030020) $fatal(1,"FORMAT");
        bus_read(16'h0060,temp_word,2'b00);if(temp_word!==32'hc556a8e8) $fatal(1,"contract tag");
        bus_read(16'h0064,temp_word,2'b00);if(temp_word!==0) $fatal(1,"native error at reset");
        // ABORT discards a partially pre-emphasized clip, including all IP state.
        bus_write(16'h0010,32'd672,4'hf,2'b00);
        bus_write(16'h0008,32'd1,4'hf,2'b00);feed_constant(173);
        bus_write(16'h0008,32'd2,4'hf,2'b00);
        bus_read(16'h000c,temp_word,2'b00);if(temp_word!==0) $fatal(1,"abort status");
        bus_read(16'h0030,temp_word,2'b00);if(temp_word!==0) $fatal(1,"abort counter");
        // Both clips reuse the same unmodified prefix. The second START occurs
        // without a bus/system reset and must reset the native pre-emphasis.
        for(c=0;c<2;c=c+1) begin
            sent=0;received=0;finished_case=0;long_stall_done=0;
            bus_write(16'h0010,32'd672,4'hf,2'b00);
            bus_write(16'h0008,32'd1,4'hf,2'b00);
            while(!finished_case) begin
                bus_read(16'h000c,status_word,2'b00);
                if(status_word[2]) begin
                    bus_read(16'h002c,temp_word,2'b00);
                    $display("transport error=%h",temp_word);
                    bus_read(16'h0064,temp_word,2'b00);
                    $fatal(1,"clip=%0d core_error=%h sent=%0d received=%0d",c,temp_word,sent,received);
                end
                if(status_word[4]) begin
                    if(!long_stall_done) begin repeat(5000) @(negedge clk);long_stall_done=1;end
                    bus_read(16'h0018,low_word,2'b00);bus_read(16'h001c,high_word,2'b00);
                    bus_read(16'h0020,frame_word,2'b00);bus_read(16'h0024,meta_word,2'b00);
                    if(received>=26) $fatal(1,"extra output");
                    if(low_word!==expected_mfcc[received] || high_word!==32'd0 ||
                       frame_word!==32'(received/13) || meta_word!==((received%13) | ((received%13==12)?32'h10000:32'd0)))
                        $fatal(1,"result mismatch clip=%0d item=%0d actual=%h expected=%h high=%h frame=%0d meta=%h",c,received,low_word,expected_mfcc[received],high_word,frame_word,meta_word);
                    if(low_word[30:23]===8'hff) $fatal(1,"NaN/Inf result");
                    if(received==0) begin
                        bus_read(16'h0018,temp_word,2'b00);if(temp_word!==low_word) $fatal(1,"read popped output");
                    end
                    bus_write(16'h0028,32'd1,4'hf,2'b00);
                    received=received+1;total_output=total_output+1;
                end
                if(status_word[3] && sent<672) begin
                    if(sent%79==5) repeat(3) @(negedge clk);
                    bus_write(16'h0014,{16'd0,pcm[sent]},4'hf,2'b00);sent=sent+1;
                end
                if(status_word[1]) begin
                    if(sent!=672 || received!=26) $fatal(1,"early DONE");
                    finished_case=1;
                end
            end
            bus_read(16'h000c,temp_word,2'b00);if(temp_word!==2) $fatal(1,"final status=%h",temp_word);
            bus_read(16'h002c,temp_word,2'b00);if(temp_word!==0) $fatal(1,"transport error");
            bus_read(16'h0064,temp_word,2'b00);if(temp_word!==0) $fatal(1,"native error");
            bus_read(16'h0030,temp_word,2'b00);if(temp_word!==672) $fatal(1,"written count");
            bus_read(16'h0034,temp_word,2'b00);if(temp_word!==672) $fatal(1,"consumed count");
            bus_read(16'h0038,temp_word,2'b00);if(temp_word!==26) $fatal(1,"captured count");
            bus_read(16'h003c,temp_word,2'b00);if(temp_word!==26) $fatal(1,"popped count");
            repeat(64) @(negedge clk);
            bus_read(16'h000c,temp_word,2'b00);if(temp_word!==2) $fatal(1,"post-DONE stale output");
            $display("FP32_VENDOR_AXI_CLIP_PASS clip=%0d samples=%0d frames=2 output=%0d cycles=%0d",c,sent,received,cycles);
        end
        if(total_output!=52 || rstall==0 || bstall==0 || core_stall==0 || native_starts!=3 || native_ends!=2 || native_dones!=2)
            $fatal(1,"coverage counts starts=%0d ends=%0d done=%0d rstall=%0d bstall=%0d corestall=%0d",native_starts,native_ends,native_dones,rstall,bstall,core_stall);
        fd=$fopen({run_dir,"/vendor_axi_simulation.json"},"w");
        if(!fd) $fatal(1,"cannot save simulation result");
        $fdisplay(fd,"{\"status\":\"PASS\",\"clips\":2,\"samples_per_clip\":672,\"frames_per_clip\":2,\"mfcc_records\":52,\"mismatches\":0,\"cycles\":%0d,\"AXI_reads\":%0d,\"AXI_writes\":%0d,\"R_stall_cycles\":%0d,\"B_stall_cycles\":%0d,\"native_stall_cycles\":%0d,\"long_output_stall_per_clip\":5000,\"abort_partial_PCM\":173,\"native_starts\":3,\"native_ends\":2,\"native_dones\":2,\"actual_vendor_IP\":true}",cycles,reads,writes,rstall,bstall,core_stall);
        $fclose(fd);$display("FP32_VENDOR_AXI_PASS");$finish;
    end
endmodule
