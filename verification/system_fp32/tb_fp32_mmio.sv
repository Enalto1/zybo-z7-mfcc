`timescale 1ns/1ps
// Fake-core tests isolate the bus/slot/error contract from MFCC arithmetic.
module tb_fp32_mmio;
    reg clk=0; always #5 clk=~clk;
    reg rst_n=0;
    reg [15:0] awaddr=0,araddr=0;
    reg [2:0] awprot=3'b111,arprot=3'b111;
    reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
    reg [31:0] wdata=0;reg [3:0] wstrb=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp;wire [31:0] rdata;
    wire clip_start,abort_core,pcm_valid,pcm_last,output_ready;
    wire signed [15:0] pcm;
    reg pcm_ready=0,output_valid=0,output_last=0,output_error=0,manual_done=0,empty_auto=0;
    reg [63:0] output_data=0;reg [31:0] output_frame=0;reg [3:0] output_index=0;
    reg signed [7:0] output_bfp=0;
    reg [11:0] core_error_detail=0;
    reg metadata_error=0;
    wire clip_done=manual_done || (empty_auto && clip_start && pcm_last);
    integer cycles=0,reads=0,writes=0,rstalls=0,bstalls=0,pcm_stalls=0,tests=0;
    integer starts=0,aborts=0,last_samples=0,fd,k;
    reg old_rstall=0,old_bstall=0,old_pcm_stall=0;
    reg [33:0] held_r;reg [1:0] held_b;reg [16:0] held_pcm;
    reg [31:0] value;
    string run_dir;

    fp32_mmio DUT (
        .s_axi_aclk(clk),.s_axi_aresetn(rst_n),
        .s_axi_awaddr(awaddr),.s_axi_awprot(awprot),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(arprot),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
        .o_clip_start(clip_start),.o_abort(abort_core),.o_pcmvalid(pcm_valid),.i_pcmready(pcm_ready),
        .o_pcm(pcm),.o_pcm_last(pcm_last),.i_outputvalid(output_valid),.o_outputready(output_ready),
        .i_data64(output_data),.i_frame32(output_frame),.i_index4(output_index),.i_bfp8(output_bfp),
        .i_last(output_last),.i_error(output_error),.i_metadata_error(metadata_error),
        .i_core_error_detail(core_error_detail),.i_clipdone(clip_done)
    );
    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>500000) $fatal(1,"MMIO unit watchdog tests=%0d",tests);
        if(!rst_n) begin old_rstall=0;old_bstall=0;old_pcm_stall=0;end
        else begin
            if(old_rstall && (!rvalid || {rdata,rresp}!==held_r)) $fatal(1,"R response changed while stalled");
            if(old_bstall && (!bvalid || bresp!==held_b)) $fatal(1,"B response changed while stalled");
            old_rstall=rvalid&&!rready;old_bstall=bvalid&&!bready;
            held_r={rdata,rresp};held_b=bresp;
            if(old_rstall) rstalls=rstalls+1;
            if(old_bstall) bstalls=bstalls+1;
            if(clip_start) begin starts=starts+1;if(empty_auto&&!pcm_last) $fatal(1,"empty start lacks last");end
            if(abort_core) aborts=aborts+1;
            if(clip_start||abort_core||clip_done) old_pcm_stall=0;
            else begin
                if(old_pcm_stall && (!pcm_valid || {pcm,pcm_last}!==held_pcm)) $fatal(1,"PCM slot changed under backpressure");
                old_pcm_stall=pcm_valid&&!pcm_ready;held_pcm={pcm,pcm_last};
                if(old_pcm_stall) pcm_stalls=pcm_stalls+1;
            end
            if(pcm_valid&&pcm_ready&&pcm_last) last_samples=last_samples+1;
        end
    end
    task reset_all;
        begin
            @(negedge clk);rst_n=0;awvalid=0;wvalid=0;bready=0;arvalid=0;rready=0;
            pcm_ready=0;output_valid=0;output_error=0;manual_done=0;empty_auto=0;
            repeat(6) @(negedge clk);rst_n=1;repeat(4) @(negedge clk);
        end
    endtask
    task put_aw(input [15:0] address);
        begin
            @(negedge clk);awaddr=address;awvalid=1;
            while(!awready) @(negedge clk);
            @(posedge clk);@(negedge clk);awvalid=0;
        end
    endtask
    task put_w(input [31:0] data,input [3:0] strobes);
        begin
            @(negedge clk);wdata=data;wstrb=strobes;wvalid=1;
            while(!wready) @(negedge clk);
            @(posedge clk);@(negedge clk);wvalid=0;
        end
    endtask
    task bus_write(input [15:0] address,input [31:0] data,input [3:0] strobes,input [1:0] response);
        begin
            case(writes%3)
                0:begin put_aw(address);repeat(3) @(negedge clk);put_w(data,strobes);end
                1:begin put_w(data,strobes);repeat(4) @(negedge clk);put_aw(address);end
                default:fork put_aw(address);put_w(data,strobes);join
            endcase
            while(!bvalid) @(negedge clk);
            if(bresp!==response) $fatal(1,"unit BRESP addr=%h data=%h actual=%b expected=%b",address,data,bresp,response);
            repeat(3) @(negedge clk);
            bready=1;@(posedge clk);@(negedge clk);bready=0;writes=writes+1;
        end
    endtask
    task bus_read(input [15:0] address,output [31:0] data,input [1:0] response);
        begin
            @(negedge clk);araddr=address;arvalid=1;
            while(!arready) @(negedge clk);
            @(posedge clk);@(negedge clk);arvalid=0;
            while(!rvalid) @(negedge clk);
            data=rdata;
            if(rresp!==response) $fatal(1,"unit RRESP addr=%h actual=%b expected=%b",address,rresp,response);
            repeat(4) @(negedge clk);
            rready=1;@(posedge clk);@(negedge clk);rready=0;reads=reads+1;
        end
    endtask
    task expect_read(input [15:0] address,input [31:0] expected,input [1:0] response);
        reg [31:0] actual;
        begin
            bus_read(address,actual,response);
            if(actual!==expected) $fatal(1,"unit register addr=%h actual=%h expected=%h tests=%0d",address,actual,expected,tests);
        end
    endtask
    task start_clip(input [31:0] samples);
        begin
            bus_write(16'h0010,samples,4'hf,2'b00);
            bus_write(16'h0008,32'd1,4'hf,2'b00);
        end
    endtask
    task abort_clip;
        begin
            bus_write(16'h0008,32'd2,4'hf,2'b00);
            output_valid=0;output_error=0;manual_done=0;
            expect_read(16'h000c,32'd0,2'b00);
            expect_read(16'h002c,32'd0,2'b00);
        end
    endtask
    task feed(input integer count);
        integer j;
        begin
            pcm_ready=1;
            for(j=0;j<count;j=j+1) bus_write(16'h0014,{16'hfedc,16'(j)},4'hf,2'b00);
            repeat(2) @(negedge clk);
        end
    endtask
    task emit(input [63:0] data,input [31:0] frame,input [3:0] index,
              input signed [7:0] bfp,input last,input error,input finish);
        begin
            @(negedge clk);output_data=data;output_frame=frame;output_index=index;
            output_bfp=bfp;output_last=last;output_error=error;output_valid=1;
            while(!output_ready) @(negedge clk);
            manual_done=finish;
            @(posedge clk);@(negedge clk);output_valid=0;output_error=0;manual_done=0;
        end
    endtask
    task done_pulse;
        begin @(negedge clk);manual_done=1;@(posedge clk);@(negedge clk);manual_done=0;end
    endtask
    task command_and_bad_read(input [31:0] command);
        begin
            @(negedge clk);awaddr=16'h0008;awvalid=1;wdata=command;wstrb=4'hf;wvalid=1;
            if(!awready||!wready||!arready) $fatal(1,"concurrent test channels not idle");
            @(posedge clk);@(negedge clk);awvalid=0;wvalid=0;araddr=16'h0068;arvalid=1;
            // Both stored write halves commit on the same edge as this AR.
            @(posedge clk);@(negedge clk);arvalid=0;
            if(!bvalid||bresp!==0||!rvalid||rresp!==2'b10||rdata!==0) $fatal(1,"simultaneous command/read response");
            repeat(4) @(negedge clk);bready=1;rready=1;
            @(posedge clk);@(negedge clk);bready=0;rready=0;writes=writes+1;reads=reads+1;
            expect_read(16'h002c,32'd1,2'b00);
        end
    endtask

    initial begin
        run_dir=".";
        if($value$plusargs("RUN=%s",run_dir)) begin end
        reset_all();
        expect_read(16'h0000,32'h4d464343,2'b00);
        expect_read(16'h0004,32'h00010001,2'b00);
        expect_read(16'h0040,32'd2,2'b00);
        expect_read(16'h0044,32'h00030020,2'b00);
        expect_read(16'h0048,32'd512,2'b00);expect_read(16'h004c,32'd160,2'b00);
        expect_read(16'h0050,32'd13,2'b00);expect_read(16'h0054,32'd262144,2'b00);
        expect_read(16'h0060,32'hc556a8e8,2'b00);tests=tests+1;

        bus_write(16'h0010,32'd262144,4'hf,2'b00);
        bus_write(16'h0010,32'd262145,4'hf,2'b10);
        bus_write(16'h0010,32'hffffffff,4'hf,2'b10);
        expect_read(16'h0010,32'd262144,2'b00);expect_read(16'h002c,32'd2,2'b00);
        bus_write(16'h0010,32'd511,4'h0,2'b10);bus_write(16'h0011,32'd7,4'hf,2'b10);
        bus_write(16'h0000,32'd1,4'hf,2'b10);bus_write(16'hffff,32'd0,4'hf,2'b10);
        expect_read(16'h0008,32'd0,2'b10);expect_read(16'h0014,32'd0,2'b10);
        expect_read(16'h0028,32'd0,2'b10);expect_read(16'h0001,32'd0,2'b10);
        expect_read(16'hffff,32'd0,2'b10);expect_read(16'h0010,32'd262144,2'b00);
        expect_read(16'h002c,32'd3,2'b00);tests=tests+1;
        abort_clip();expect_read(16'h0010,32'd262144,2'b00);

        // Empty completion may be concurrent with the registered start pulse.
        empty_auto=1;start_clip(32'd0);empty_auto=0;
        expect_read(16'h000c,32'd2,2'b00);expect_read(16'h0030,0,0);expect_read(16'h0038,0,0);
        bus_write(16'h0008,32'd4,4'hf,2'b00);expect_read(16'h000c,0,0);tests=tests+1;

        // Full input slot rejects a second write without corrupting the first.
        start_clip(32'd2);pcm_ready=0;
        bus_write(16'h0014,32'habcd8001,4'hf,2'b00);
        if(!pcm_valid||pcm!==16'h8001||pcm_last) $fatal(1,"first PCM holding slot");
        bus_write(16'h0014,32'h00001111,4'hf,2'b10);
        repeat(11) @(negedge clk);expect_read(16'h0030,1,0);expect_read(16'h0034,0,0);
        pcm_ready=1;repeat(2) @(negedge clk);pcm_ready=0;
        bus_write(16'h0014,32'h12347fff,4'hf,2'b00);
        if(!pcm_valid||pcm!==16'h7fff||!pcm_last) $fatal(1,"count-derived final PCM tag");
        pcm_ready=1;repeat(2) @(negedge clk);
        bus_write(16'h0014,32'd3,4'hf,2'b10);
        expect_read(16'h0030,2,0);expect_read(16'h0034,2,0);expect_read(16'h002c,4,0);
        done_pulse();expect_read(16'h000c,6,0);tests=tests+1;abort_clip();

        // Raw signed payload and metadata remain unchanged until explicit POP.
        start_clip(32'd512);feed(512);
        emit(64'hfffffffffffffffe,0,0,-8'sd2,0,0,0);
        if(output_ready) $fatal(1,"occupied output slot did not stop core");
        expect_read(16'h0018,32'hfffffffe,0);expect_read(16'h001c,32'hffffffff,0);
        expect_read(16'h0020,0,0);expect_read(16'h0024,32'h0000fe00,0);
        repeat(23) @(negedge clk);expect_read(16'h0018,32'hfffffffe,0);
        bus_write(16'h0028,32'd2,4'hf,2'b10);expect_read(16'h0038,1,0);expect_read(16'h003c,0,0);
        // An already-issued R response survives ABORT, while the record flushes.
        @(negedge clk);araddr=16'h0018;arvalid=1;
        @(posedge clk);@(negedge clk);arvalid=0;
        while(!rvalid) @(negedge clk);
        bus_write(16'h0008,32'd2,4'hf,2'b00);
        if(!rvalid||rdata!==32'hfffffffe||rresp!==0) $fatal(1,"ABORT reset pending R channel");
        rready=1;@(posedge clk);@(negedge clk);rready=0;reads=reads+1;
        expect_read(16'h000c,0,0);expect_read(16'h0038,0,0);expect_read(16'h003c,0,0);
        expect_read(16'h0018,0,2'b10);tests=tests+1;abort_clip();

        start_clip(32'd512);emit(64'h123456789abcdef0,0,0,8'sd3,0,1,0);
        expect_read(16'h002c,32'd8,0);expect_read(16'h0024,32'h00020300,0);
        expect_read(16'h0018,32'h9abcdef0,0);expect_read(16'h001c,32'h12345678,0);
        tests=tests+1;abort_clip();

        start_clip(32'd512);emit(1,1,0,0,0,0,0);
        expect_read(16'h002c,32'd16,0);tests=tests+1;abort_clip();
        start_clip(32'd512);emit(1,0,1,0,0,0,0);
        expect_read(16'h002c,32'd16,0);tests=tests+1;abort_clip();
        start_clip(32'd512);emit(1,0,0,0,1,0,0);
        expect_read(16'h002c,32'd16,0);tests=tests+1;abort_clip();
        start_clip(32'd512);emit(1,0,0,8'sd4,0,0,0);bus_write(16'h0028,1,4'hf,0);
        emit(2,0,1,8'sd5,0,0,0);expect_read(16'h002c,32'd16,0);
        tests=tests+1;abort_clip();

        // Completion faults finish BUSY and expose DONE instead of hanging.
        start_clip(32'd1);pcm_ready=0;bus_write(16'h0014,32'd42,4'hf,0);
        done_pulse();expect_read(16'h000c,6,0);expect_read(16'h002c,32'd32,0);
        if(pcm_valid) $fatal(1,"PCM stream remained live after failed completion");
        tests=tests+1;abort_clip();
        start_clip(32'd512);feed(512);done_pulse();
        expect_read(16'h002c,32'd32,0);expect_read(16'h000c,6,0);tests=tests+1;abort_clip();

        // 672 samples make two frames; completion captures the final output
        // on the same edge, and DONE is independent of the final ARM POP.
        start_clip(32'd672);feed(672);
        for(k=0;k<26;k=k+1) begin
            emit(64'(k),32'(k/13),4'(k%13),k<13?-8'sd2:8'sd7,k%13==12,0,k==25);
            if(k<25) bus_write(16'h0028,1,4'hf,0);
        end
        expect_read(16'h000c,32'd18,0);expect_read(16'h0030,32'd672,0);expect_read(16'h0034,32'd672,0);
        expect_read(16'h0038,32'd26,0);expect_read(16'h003c,32'd25,0);
        expect_read(16'h0024,32'h0001070c,0);expect_read(16'h002c,0,0);
        bus_write(16'h0010,1,4'hf,2'b10);bus_write(16'h0008,1,4'hf,2'b10);
        expect_read(16'h0010,32'd672,0);bus_write(16'h0008,4,4'hf,0);
        expect_read(16'h000c,32'd16,0);expect_read(16'h0038,32'd26,0);
        bus_write(16'h0028,1,4'hf,0);expect_read(16'h003c,32'd26,0);expect_read(16'h000c,0,0);
        tests=tests+1;

        command_and_bad_read(32'd4);
        bus_write(16'h0010,32'd1,4'hf,0);command_and_bad_read(32'd1);
        command_and_bad_read(32'd2);expect_read(16'h000c,32'd4,0);
        bus_write(16'h0008,4,4'hf,0);expect_read(16'h000c,0,0);tests=tests+1;

        // Native errors need no result transaction and retain all12 bits.
        start_clip(32'd1);
        @(negedge clk);core_error_detail=12'h807;output_error=1;
        @(negedge clk);core_error_detail=12'h209;
        @(negedge clk);core_error_detail=0;output_error=0;
        expect_read(16'h0064,32'h00000807,0);expect_read(16'h002c,32'd8,0);
        bus_write(16'h0064,32'd0,4'hf,2'b10);
        expect_read(16'h0064,32'h00000807,0);
        abort_clip();expect_read(16'h0064,0,0);
        start_clip(32'd1);expect_read(16'h0064,0,0);
        @(negedge clk);metadata_error=1;output_error=1;
        @(negedge clk);metadata_error=0;output_error=0;
        expect_read(16'h002c,32'd24,0);expect_read(16'h0064,0,0);
        abort_clip();tests=tests+1;

        if(tests!=15||rstalls==0||bstalls==0||pcm_stalls==0||last_samples<3||starts==0||aborts==0)
            $fatal(1,"unit coverage missing tests=%0d last=%0d",tests,last_samples);
        fd=$fopen({run_dir,"/fp32_mmio_unit_simulation.json"},"w");
        $fdisplay(fd,"{\"status\":\"PASS\",\"tests\":%0d,\"cycles\":%0d,\"reads\":%0d,\"writes\":%0d,\"R_stalls\":%0d,\"B_stalls\":%0d,\"PCM_stalls\":%0d,\"immediate_empty_done\":true,\"same_cycle_capture_done\":true,\"abort_preserves_R\":true,\"same_cycle_bad_read_priority\":true,\"all_six_error_flags\":true}",tests,cycles,reads,writes,rstalls,bstalls,pcm_stalls);
        $fclose(fd);$display("FP32_MMIO_UNIT_PASS tests=%0d cycles=%0d",tests,cycles);$finish;
    end
endmodule
