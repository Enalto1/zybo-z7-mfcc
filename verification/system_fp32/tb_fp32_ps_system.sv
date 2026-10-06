`timescale 1ns/1ps
// Behavioral PS7 VIP transactions through the generated GP0/interconnect/reset
// system. This does not execute ARM instructions or model physical DDR timing.
`define PS_VIP DUT.zybo_z7_20_fp32_i.processing_system7_0.inst
`define PL_CLK DUT.zybo_z7_20_fp32_i.processing_system7_0.FCLK_CLK0
`define PL_RST_N DUT.zybo_z7_20_fp32_i.fp32_accel_0.s_axi_aresetn
module tb_fp32_ps_system;
    localparam [31:0] BASE = 32'h43c00000;
    localparam integer SAMPLE_COUNT = 672;
    localparam integer OUTPUT_COUNT = 26;
    reg ps_clk = 0;
    reg ps_porb = 0;
    reg ps_srstb = 0;
    always #15 ps_clk = ~ps_clk;
    wire [14:0] ddr_addr;
    wire [2:0] ddr_ba;
    wire ddr_cas_n,ddr_ck_n,ddr_ck_p,ddr_cke,ddr_cs_n;
    wire [3:0] ddr_dm,ddr_dqs_n,ddr_dqs_p;
    wire [31:0] ddr_dq;
    wire ddr_odt,ddr_ras_n,ddr_reset_n,ddr_we_n;
    wire [53:0] mio;
    wire ddr_vrn,ddr_vrp,ps_clk_pin,ps_porb_pin,ps_srstb_pin;
    assign ps_clk_pin = ps_clk;
    assign ps_porb_pin = ps_porb;
    assign ps_srstb_pin = ps_srstb;
    assign ddr_vrn = 1'b0;
    assign ddr_vrp = 1'b1;
    zybo_z7_20_fp32_wrapper DUT (
        .DDR_addr(ddr_addr),.DDR_ba(ddr_ba),.DDR_cas_n(ddr_cas_n),
        .DDR_ck_n(ddr_ck_n),.DDR_ck_p(ddr_ck_p),.DDR_cke(ddr_cke),
        .DDR_cs_n(ddr_cs_n),.DDR_dm(ddr_dm),.DDR_dq(ddr_dq),
        .DDR_dqs_n(ddr_dqs_n),.DDR_dqs_p(ddr_dqs_p),.DDR_odt(ddr_odt),
        .DDR_ras_n(ddr_ras_n),.DDR_reset_n(ddr_reset_n),.DDR_we_n(ddr_we_n),
        .FIXED_IO_ddr_vrn(ddr_vrn),.FIXED_IO_ddr_vrp(ddr_vrp),
        .FIXED_IO_mio(mio),.FIXED_IO_ps_clk(ps_clk_pin),
        .FIXED_IO_ps_porb(ps_porb_pin),.FIXED_IO_ps_srstb(ps_srstb_pin)
    );
    reg [15:0] pcm [0:SAMPLE_COUNT-1];
    reg [31:0] mfcc [0:OUTPUT_COUNT-1];
    reg [31:0] status_word,temp_word,low_word,high_word,frame_word,meta_word;
    reg [63:0] expected64;
    reg finished = 0;
    integer cycles=0,reads=0,writes=0,reset_checks=0,error_checks=0;
    integer hold_checks=0,empty_checks=0,abort_checks=0;
    integer sent=0,received=0,negative_samples=0,negative_outputs=0,n,fd;
    realtime clock_edge,clock_period;
    string run_dir;
    always @(posedge `PL_CLK) begin
        cycles=cycles+1;
        if(cycles%100000==0)
            $display("PS_SYSTEM_PROGRESS cycles=%0d sent=%0d received=%0d",cycles,sent,received);
    end
    initial begin
        #20000000;
        $fatal(1,"PS system watchdog cycles=%0d sent=%0d received=%0d",cycles,sent,received);
    end

    // processing_system7_vip_v1_0_21 has max_transfer_bytes=256. Each call
    // issues one aligned 32-bit access; only payload[31:0] is used here.
    task automatic bus_write(input [15:0] offset,input [31:0] value,input [1:0] expected_response);
        reg [2047:0] payload;
        reg [1:0] response;
        begin
            payload=0;payload[31:0]=value;response=2'bxx;
            `PS_VIP.write_data(BASE+{16'd0,offset},9'd4,payload,response);
            if(response!==expected_response)
                $fatal(1,"PS GP0 BRESP offset=%h got=%b expected=%b",offset,response,expected_response);
            writes=writes+1;
        end
    endtask
    task automatic bus_read(input [15:0] offset,output [31:0] value,input [1:0] expected_response);
        reg [2047:0] payload;
        reg [1:0] response;
        begin
            payload='x;response=2'bxx;
            `PS_VIP.read_data(BASE+{16'd0,offset},9'd4,payload,response);
            value=payload[31:0];
            if(response!==expected_response || $isunknown(value))
                $fatal(1,"PS GP0 R offset=%h data=%h response=%b expected=%b",offset,value,response,expected_response);
            reads=reads+1;
        end
    endtask
    task automatic expect_register(input [15:0] offset,input [31:0] expected);
        reg [31:0] value;
        begin
            bus_read(offset,value,2'b00);
            if(value!==expected) $fatal(1,"PS register offset=%h got=%h expected=%h",offset,value,expected);
        end
    endtask
    task automatic fabric_reset;
        integer wait_clocks;
        begin
            // No outstanding VIP transaction exists when this task is called.
            `PS_VIP.fpga_soft_reset(32'h00000001);
            repeat(32) @(negedge `PL_CLK);
            if(`PL_RST_N!==1'b0) $fatal(1,"PS reset failed to assert peripheral reset");
            repeat(8) begin
                @(negedge `PL_CLK);
                if(`PL_RST_N!==1'b0) $fatal(1,"Peripheral reset was not held");
            end
            `PS_VIP.fpga_soft_reset(32'h00000000);
            wait_clocks=0;
            while(`PL_RST_N!==1'b1 && wait_clocks<1024) begin
                @(negedge `PL_CLK);wait_clocks=wait_clocks+1;
            end
            if(`PL_RST_N!==1'b1 || wait_clocks==0) $fatal(1,"Reset release/synchronization failed");
            repeat(8) @(negedge `PL_CLK);
            expect_register(16'h000c,32'd0);
            expect_register(16'h0010,32'd0);
            expect_register(16'h002c,32'd0);
            expect_register(16'h0030,32'd0);
            expect_register(16'h0034,32'd0);
            expect_register(16'h0038,32'd0);
            expect_register(16'h003c,32'd0);
            reset_checks=reset_checks+1;
        end
    endtask
    task automatic feed_prefix(input integer count);
        integer k;
        reg [31:0] status_value;
        begin
            for(k=0;k<count;k=k+1) begin
                bus_read(16'h000c,status_value,2'b00);
                while(!status_value[3]) begin
                    if(status_value[2]) $fatal(1,"Error while feeding reset/abort prelude");
                    bus_read(16'h000c,status_value,2'b00);
                end
                bus_write(16'h0014,{16'ha5a5,pcm[k]},2'b00);
            end
        end
    endtask
    initial begin
        if(!$value$plusargs("RUN=%s",run_dir)) $fatal(1,"RUN missing");
        $readmemh({run_dir,"/pcm.mem"},pcm,0,SAMPLE_COUNT-1);
        $readmemh({run_dir,"/mfcc.mem"},mfcc,0,OUTPUT_COUNT-1);
        for(n=0;n<SAMPLE_COUNT;n=n+1) begin
            if($isunknown(pcm[n])) $fatal(1,"Missing PCM vector %0d",n);
            if(pcm[n][15]) negative_samples=negative_samples+1;
        end
        for(n=0;n<OUTPUT_COUNT;n=n+1) begin
            if($isunknown(mfcc[n])) $fatal(1,"Missing MFCC vector %0d",n);
            if(mfcc[n][31]) negative_outputs=negative_outputs+1;
        end
        if(negative_samples==0 || negative_outputs==0)
            $fatal(1,"PS development vectors must cover negative PCM and MFCC");
        $display("PS_SYSTEM_BEGIN development_samples=%0d expected_outputs=%0d",SAMPLE_COUNT,OUTPUT_COUNT);
        repeat(20) @(negedge ps_clk);
        ps_porb=1;ps_srstb=1;
        `PS_VIP.set_debug_level_info(0);
        `PS_VIP.set_stop_on_error(1);
        repeat(20) @(negedge ps_clk);
        @(posedge `PL_CLK);clock_edge=$realtime;
        @(posedge `PL_CLK);clock_period=$realtime-clock_edge;
        if(clock_period<9.99 || clock_period>10.01) $fatal(1,"FCLK0 period %0f ns",clock_period);
        fabric_reset();
        expect_register(16'h0000,32'h4d464343);
        expect_register(16'h0004,32'h00010001);
        expect_register(16'h0040,32'd2);
        expect_register(16'h0044,32'h00030020);
        expect_register(16'h0048,32'd512);
        expect_register(16'h004c,32'd160);
        expect_register(16'h0050,32'd13);
        expect_register(16'h0060,32'hc556a8e8);
        // AXI3 GP0 -> interconnect conversion must preserve SLVERR as well as data.
        bus_write(16'h0000,32'd1,2'b10);
        bus_read(16'h0068,temp_word,2'b10);
        if(temp_word!==0) $fatal(1,"Illegal register read did not return zero");
        expect_register(16'h002c,32'd1);error_checks=error_checks+2;
        bus_write(16'h0008,32'd4,2'b00);
        expect_register(16'h002c,32'd0);
        // Empty clips exercise start/completion without injecting a PCM beat.
        bus_write(16'h0008,32'd1,2'b00);
        bus_read(16'h000c,status_word,2'b00);
        while(!status_word[1]) begin
            if(status_word[2] || status_word[4]) $fatal(1,"Empty clip produced error/output");
            bus_read(16'h000c,status_word,2'b00);
        end
        if(status_word!==32'd2) $fatal(1,"Empty clip status %h",status_word);
        expect_register(16'h0030,32'd0);expect_register(16'h0038,32'd0);
        empty_checks=empty_checks+1;
        bus_write(16'h0008,32'd4,2'b00);
        // Software abort preserves configuration and clears partial work.
        bus_write(16'h0010,32'd672,2'b00);bus_write(16'h0008,32'd1,2'b00);
        feed_prefix(13);bus_write(16'h0008,32'd2,2'b00);
        expect_register(16'h000c,32'd0);expect_register(16'h0010,32'd672);
        expect_register(16'h0030,32'd0);expect_register(16'h0034,32'd0);
        abort_checks=abort_checks+1;
        // Actual PS fabric reset reaches interconnect and the active accelerator.
        bus_write(16'h0008,32'd1,2'b00);feed_prefix(17);fabric_reset();
        bus_write(16'h0010,32'd672,2'b00);bus_write(16'h0008,32'd1,2'b00);
        while(!finished) begin
            bus_read(16'h000c,status_word,2'b00);
            if(status_word[2]) begin
                bus_read(16'h002c,temp_word,2'b00);
                $fatal(1,"PS clip error=%h sent=%0d received=%0d",temp_word,sent,received);
            end
            if(status_word[4]) begin
                if(received>=OUTPUT_COUNT) $fatal(1,"Unexpected extra output");
                bus_read(16'h0018,low_word,2'b00);bus_read(16'h001c,high_word,2'b00);
                bus_read(16'h0020,frame_word,2'b00);bus_read(16'h0024,meta_word,2'b00);
                expected64={32'd0,mfcc[received]};
                if({high_word,low_word}!==expected64 || frame_word!==32'(received/13) ||
                   meta_word[3:0]!==4'(received%13) || meta_word[15:8]!==8'd0 ||
                   meta_word[16]!==(received%13==12) || (meta_word & 32'hfffe00f0)!==0)
                    $fatal(1,"PS output %0d got=%h expected=%h frame=%h meta=%h",received,{high_word,low_word},expected64,frame_word,meta_word);
                if(received==0 || received==13) begin
                    // Read-only polling and a prolonged software pause must not pop/change the slot.
                    repeat(5000) @(negedge `PL_CLK);
                    expect_register(16'h0018,low_word);expect_register(16'h001c,high_word);
                    expect_register(16'h0020,frame_word);expect_register(16'h0024,meta_word);
                    hold_checks=hold_checks+1;
                end
                bus_write(16'h0028,32'd1,2'b00);received=received+1;
            end
            if(status_word[3] && sent<SAMPLE_COUNT) begin
                // High half deliberately differs from sign extension: only low16 is PCM.
                bus_write(16'h0014,{16'ha5a5,pcm[sent]},2'b00);sent=sent+1;
            end
            if(status_word[1] && !status_word[0] && received==OUTPUT_COUNT) finished=1;
        end
        if(sent!=SAMPLE_COUNT) $fatal(1,"Premature DONE sent=%0d",sent);
        expect_register(16'h000c,32'd2);expect_register(16'h002c,32'd0);
        expect_register(16'h0064,32'd0);
        expect_register(16'h0030,32'd672);expect_register(16'h0034,32'd672);
        expect_register(16'h0038,32'd26);expect_register(16'h003c,32'd26);
        fd=$fopen({run_dir,"/ps_simulation.json"},"w");
        if(!fd) $fatal(1,"Cannot write PS simulation result");
        $fwrite(fd,"{\n  \"status\":\"PASS\",\n  \"scope\":\"PS7 VIP GP0, generated AXI interconnect, proc_sys_reset, actual IP04 FP32 MFCC\",\n");
        $fwrite(fd,"  \"frames\":2,\"samples\":672,\"mfcc_words\":26,\"clock_period_ns\":%0.3f,\n",clock_period);
        $fwrite(fd,"  \"axi_reads\":%0d,\"axi_writes\":%0d,\"cycles\":%0d,\n",reads,writes,cycles);
        $fwrite(fd,"  \"reset_checks\":%0d,\"error_checks\":%0d,\"empty_checks\":%0d,\"abort_checks\":%0d,\"output_hold_checks\":%0d,\n",reset_checks,error_checks,empty_checks,abort_checks,hold_checks);
        $fwrite(fd,"  \"negative_pcm_samples\":%0d,\"negative_mfcc_words\":%0d,\n",negative_samples,negative_outputs);
        $fwrite(fd,"  \"arm_instructions_executed\":false,\"physical_board_accessed\":false,\"physical_ddr_timing_verified\":false\n}\n");
        $fclose(fd);
        $display("FP32_PS_SYSTEM_PASS samples=%0d outputs=%0d cycles=%0d reads=%0d writes=%0d",sent,received,cycles,reads,writes);
        $finish;
    end
endmodule
`undef PS_VIP
`undef PL_CLK
`undef PL_RST_N
