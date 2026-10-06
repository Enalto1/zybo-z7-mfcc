`timescale 1ns/1ps
// Behavioral PS7 VIP transactions through the generated GP0/interconnect/reset
// system. This does not execute ARM instructions or model physical DDR timing.
`define PS_VIP DUT.zybo_dma_i.processing_system7_0.inst
`define PL_CLK DUT.zybo_dma_i.processing_system7_0.FCLK_CLK0
`define PL_RST_N DUT.zybo_dma_i.mfcc_dma_0.s_axi_aresetn
`define DMA_CORE DUT.zybo_dma_i.mfcc_dma_0
`define CORE_RST_N DUT.zybo_dma_i.mfcc_dma_0.inst.core_rst_n
module tb_dma_system;
    localparam [31:0] CORE_BASE = 32'h43c00000;
    localparam [31:0] DMA_BASE = 32'h40400000;
    localparam [31:0] INPUT_BASE = 32'h02000000;
    localparam [31:0] OUTPUT_BASE = 32'h02100000;
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
    zybo_dma_wrapper DUT (
        .DDR_addr(ddr_addr),.DDR_ba(ddr_ba),.DDR_cas_n(ddr_cas_n),
        .DDR_ck_n(ddr_ck_n),.DDR_ck_p(ddr_ck_p),.DDR_cke(ddr_cke),
        .DDR_cs_n(ddr_cs_n),.DDR_dm(ddr_dm),.DDR_dq(ddr_dq),
        .DDR_dqs_n(ddr_dqs_n),.DDR_dqs_p(ddr_dqs_p),.DDR_odt(ddr_odt),
        .DDR_ras_n(ddr_ras_n),.DDR_reset_n(ddr_reset_n),.DDR_we_n(ddr_we_n),
        .FIXED_IO_ddr_vrn(ddr_vrn),.FIXED_IO_ddr_vrp(ddr_vrp),
        .FIXED_IO_mio(mio),.FIXED_IO_ps_clk(ps_clk_pin),
        .FIXED_IO_ps_porb(ps_porb_pin),.FIXED_IO_ps_srstb(ps_srstb_pin)
    );

    reg [15:0] pcm [0:671];
    reg [31:0] records [0:155];
    integer lengths[0:5];
    integer core_kind=0,cycles=0,reads=0,writes=0,clip=0,n,fd;
    integer selected_samples=0,selected_frames=0,input_beats=0,output_words=0;
    integer checked_samples=0,checked_frames=0,checked_records=0;
    integer irq_mm2s=0,irq_s2mm=0,irq_core=0,canary_checks=0;
    integer input_stalls=0,output_stalls=0,held_checks=0,reset_checks=0;
    integer reset_span=0,reset_edges=0;
    integer partial_abort_consumed=0,partial_wait=0;
    reg reset_previous=0,monitor_clip=0,held_input=0,held_output=0;
    reg [36:0] held_input_word,held_output_word;
    reg [31:0] temp,status,low_word;
    reg [15:0] interrupt_status;
    realtime clock_edge,clock_period;
    string run_dir;
    initial begin
        #1000;
        $display("DMA_CLOCK_DIAGNOSTIC observed=%b core=%b vip=%b generator=%b frequency=%f half_period=%f cycles=%0d",`PL_CLK,`DMA_CORE.s_axi_aclk,`PS_VIP.FCLK_CLK0,`PS_VIP.gen_clk.clk0,`PS_VIP.C_FCLK_CLK0_FREQ,`PS_VIP.gen_clk.clk0_p,cycles);
        $fflush();
        #9000;
        if(cycles==0) $fatal(1,"No generated FCLK0 edges within10us");
    end
    initial begin
        #20000000;
        $fatal(1,"DMA simulation-time watchdog cycles=%0d clip=%0d input=%0d output=%0d",cycles,clip,input_beats,output_words);
    end
    always @(posedge `PL_CLK) begin
        cycles=cycles+1;
        if(cycles%100000==0) begin
            $display("DMA_SYSTEM_PROGRESS cycles=%0d clip=%0d input=%0d output=%0d",cycles,clip,input_beats,output_words);
            $fflush();
        end
        if(cycles>2000000) $fatal(1,"DMA system watchdog clip=%0d input=%0d output=%0d",clip,input_beats,output_words);
        if(core_kind==2 && !$isunknown(`CORE_RST_N)) begin
            if(`CORE_RST_N!==reset_previous) begin
                $display("DMA_NATIVE_RESET_EDGE cycle=%0d previous=%b width=%0d next=%b",cycles,reset_previous,reset_span,`CORE_RST_N);
                $fflush();
                if(reset_edges>0 && ((!reset_previous && reset_span<16) || (reset_previous && reset_span<4)))
                    $fatal(1,"FP32 adapter low16/high4 reset guard violated");
                reset_previous=`CORE_RST_N;reset_span=0;reset_edges=reset_edges+1;
            end
            reset_span=reset_span+1;
        end
        if(!`PL_RST_N || !monitor_clip) begin held_input=0;held_output=0;end
        else begin
            if(held_input && (!`DMA_CORE.s_axis_pcm_tvalid || held_input_word!=={`DMA_CORE.s_axis_pcm_tdata,`DMA_CORE.s_axis_pcm_tkeep,`DMA_CORE.s_axis_pcm_tlast}))
                $fatal(1,"MM2S input changed under core backpressure");
            if(held_output && (!`DMA_CORE.m_axis_result_tvalid || held_output_word!=={`DMA_CORE.m_axis_result_tdata,`DMA_CORE.m_axis_result_tkeep,`DMA_CORE.m_axis_result_tlast}))
                $fatal(1,"Result stream changed under S2MM backpressure");
            held_input=`DMA_CORE.s_axis_pcm_tvalid&&!`DMA_CORE.s_axis_pcm_tready;
            held_output=`DMA_CORE.m_axis_result_tvalid&&!`DMA_CORE.m_axis_result_tready;
            held_input_word={`DMA_CORE.s_axis_pcm_tdata,`DMA_CORE.s_axis_pcm_tkeep,`DMA_CORE.s_axis_pcm_tlast};
            held_output_word={`DMA_CORE.m_axis_result_tdata,`DMA_CORE.m_axis_result_tkeep,`DMA_CORE.m_axis_result_tlast};
            if(held_input) begin input_stalls=input_stalls+1;held_checks=held_checks+1;end
            if(held_output) begin output_stalls=output_stalls+1;held_checks=held_checks+1;end
            if(`DMA_CORE.s_axis_pcm_tvalid&&`DMA_CORE.s_axis_pcm_tready) begin
                if(input_beats>=(selected_samples+1)/2) $fatal(1,"Extra MM2S input beat");
                if(`DMA_CORE.s_axis_pcm_tdata[15:0]!==pcm[input_beats*2]) $fatal(1,"MM2S lower sample mismatch");
                if(input_beats*2+1<selected_samples && `DMA_CORE.s_axis_pcm_tdata[31:16]!==pcm[input_beats*2+1]) $fatal(1,"MM2S upper sample mismatch");
                if(`DMA_CORE.s_axis_pcm_tkeep!==((input_beats*2+1==selected_samples)?4'h3:4'hf)) $fatal(1,"MM2S TKEEP mismatch");
                if(`DMA_CORE.s_axis_pcm_tlast!==(input_beats+1==(selected_samples+1)/2)) $fatal(1,"MM2S TLAST mismatch");
                input_beats=input_beats+1;
            end
            if(`DMA_CORE.m_axis_result_tvalid&&`DMA_CORE.m_axis_result_tready) begin
                if(output_words>=selected_frames*78) $fatal(1,"Extra S2MM output word");
                if(`DMA_CORE.m_axis_result_tdata!==records[output_words] || `DMA_CORE.m_axis_result_tkeep!==4'hf ||
                   `DMA_CORE.m_axis_result_tlast!==(output_words+1==selected_frames*78))
                    $fatal(1,"S2MM stream mismatch clip=%0d word=%0d data=%h expected=%h keep=%h last=%b",clip,output_words,`DMA_CORE.m_axis_result_tdata,records[output_words],`DMA_CORE.m_axis_result_tkeep,`DMA_CORE.m_axis_result_tlast);
                output_words=output_words+1;
            end
        end
    end
    task automatic bus_write(input [31:0] address,input [31:0] value,input [1:0] expected_response);
        reg [2047:0] payload;reg [1:0] response;
        begin
            payload=0;payload[31:0]=value;response=2'bxx;
            `PS_VIP.write_data(address,9'd4,payload,response);
            if(response!==expected_response) $fatal(1,"GP0 write address=%h response=%b",address,response);
            writes=writes+1;
        end
    endtask
    task automatic bus_read(input [31:0] address,output [31:0] value,input [1:0] expected_response);
        reg [2047:0] payload;reg [1:0] response;
        begin
            payload='x;response=2'bxx;
            `PS_VIP.read_data(address,9'd4,payload,response);value=payload[31:0];
            if(response!==expected_response || $isunknown(value)) $fatal(1,"GP0 read address=%h value=%h response=%b",address,value,response);
            reads=reads+1;
        end
    endtask
    task automatic expect_register(input [31:0] address,input [31:0] expected);
        reg [31:0] value;
        begin
            bus_read(address,value,2'b00);
            if(value!==expected) $fatal(1,"Register address=%h value=%h expected=%h",address,value,expected);
        end
    endtask
    task automatic memory_write(input [31:0] address,input [31:0] value);
        reg [1023:0] payload;
        begin payload=0;payload[31:0]=value;`PS_VIP.write_mem(payload,address,8'd4);end
    endtask
    task automatic memory_read(input [31:0] address,output [31:0] value);
        reg [1023:0] payload;
        begin payload='x;`PS_VIP.read_mem(address,8'd4,payload);value=payload[31:0];end
    endtask
    task automatic memory_expect(input [31:0] address,input [31:0] expected);
        reg [31:0] value;
        begin memory_read(address,value);if(value!==expected) $fatal(1,"DDR address=%h got=%h expected=%h",address,value,expected);end
    endtask
    task automatic prepare_memory(input integer output_bytes);
        integer k;
        begin
            $display("DMA_MEMORY_PREPARE_BEGIN cycles=%0d bytes=%0d",cycles,output_bytes);$fflush();
            for(k=0;k<336;k=k+1) memory_write(INPUT_BASE+4*k,{pcm[2*k+1],pcm[2*k]});
            for(k=0;k<156;k=k+1) memory_write(OUTPUT_BASE+4*k,32'hcccccccc);
            for(k=0;k<8;k=k+1) begin
                memory_write(OUTPUT_BASE-32+4*k,32'ha55a0100+k);
                memory_write(OUTPUT_BASE+output_bytes+4*k,32'h5aa50100+k);
            end
            $display("DMA_MEMORY_PREPARE_END cycles=%0d",cycles);$fflush();
        end
    endtask
    task automatic check_memory(input integer output_bytes);
        integer k;
        begin
            for(k=0;k<output_bytes/4;k=k+1) memory_expect(OUTPUT_BASE+4*k,records[k]);
            for(k=0;k<8;k=k+1) begin
                memory_expect(OUTPUT_BASE-32+4*k,32'ha55a0100+k);
                memory_expect(OUTPUT_BASE+output_bytes+4*k,32'h5aa50100+k);
            end
            for(k=0;k<336;k=k+1) memory_expect(INPUT_BASE+4*k,{pcm[2*k+1],pcm[2*k]});
            canary_checks=canary_checks+1;
        end
    endtask
    task automatic fabric_reset;
        integer count;
        begin
            $display("DMA_FABRIC_RESET_BEGIN cycles=%0d",cycles);$fflush();
            `PS_VIP.fpga_soft_reset(32'd1);repeat(40) @(negedge `PL_CLK);
            if(`PL_RST_N!==0) $fatal(1,"Fabric reset did not assert");
            `PS_VIP.fpga_soft_reset(32'd0);count=0;
            while(`PL_RST_N!==1 && count<1024) begin @(negedge `PL_CLK);count=count+1;end
            if(`PL_RST_N!==1 || count==0) $fatal(1,"Fabric reset release failed");
            repeat(8) @(negedge `PL_CLK);
            $display("DMA_FIRST_GP0_READ_BEGIN cycles=%0d",cycles);$fflush();
            expect_register(CORE_BASE+32'h0c,32'd0);
            $display("DMA_FIRST_GP0_READ_END cycles=%0d",cycles);$fflush();
            reset_checks=reset_checks+1;
        end
    endtask
    task automatic reset_dma;
        integer count;reg [31:0] value;
        begin
            $display("DMA_SOFT_RESET_BEGIN cycles=%0d",cycles);$fflush();
            bus_write(DMA_BASE,32'd4,2'b00);count=0;value=4;
            while(value[2] && count<1024) begin bus_read(DMA_BASE,value,2'b00);count=count+1;end
            if(value[2]) $fatal(1,"DMA reset deadline");
            bus_read(DMA_BASE+32'h30,value,2'b00);if(value[2]) $fatal(1,"S2MM reset did not clear");
            bus_write(DMA_BASE+32'h04,32'h7000,2'b00);
            bus_write(DMA_BASE+32'h34,32'h7000,2'b00);
            $display("DMA_SOFT_RESET_END cycles=%0d reads=%0d",cycles,count);$fflush();
        end
    endtask
    task automatic arm_s2mm(input integer byte_count);
        begin
            $display("DMA_ARM_S2MM_BEGIN cycles=%0d bytes=%0d",cycles,byte_count);$fflush();
            bus_write(DMA_BASE+32'h30,32'h5001,2'b00);
            bus_write(DMA_BASE+32'h48,OUTPUT_BASE,2'b00);
            bus_write(DMA_BASE+32'h58,32'(byte_count),2'b00);
            $display("DMA_ARM_S2MM_END cycles=%0d",cycles);$fflush();
        end
    endtask
    task automatic start_mm2s(input integer byte_count);
        begin
            $display("DMA_START_MM2S_BEGIN cycles=%0d bytes=%0d",cycles,byte_count);$fflush();
            bus_write(DMA_BASE,32'h5001,2'b00);
            bus_write(DMA_BASE+32'h18,INPUT_BASE,2'b00);
            bus_write(DMA_BASE+32'h28,32'(byte_count),2'b00);
            $display("DMA_START_MM2S_END cycles=%0d",cycles);$fflush();
        end
    endtask
    task automatic wait_sources(input [15:0] mask);
        integer count;reg [15:0] pending;
        begin
            $display("DMA_WAIT_IRQ_BEGIN cycles=%0d mask=%h clip=%0d",cycles,mask,clip);$fflush();
            count=0;pending=0;
            while((pending&mask)!==mask && count<300000) begin
                `PS_VIP.read_interrupt(pending);
                if(pending[15:3]!==0) $fatal(1,"Unexpected IRQ_F2P high bits %h",pending);
                repeat(4) @(negedge `PL_CLK);count=count+1;
            end
            if((pending&mask)!==mask) begin
                bus_read(CORE_BASE+32'h0c,status,2'b00);$display("core status %h",status);
                bus_read(CORE_BASE+32'h2c,status,2'b00);$display("core errors %h",status);
                bus_read(CORE_BASE+32'h64,status,2'b00);$display("core detail %h",status);
                bus_read(DMA_BASE+32'h04,status,2'b00);$display("MM2S status %h",status);
                bus_read(DMA_BASE+32'h34,status,2'b00);$display("S2MM status %h",status);
                $fatal(1,"IRQ deadline mask=%h pending=%h clip=%0d",mask,pending,clip);
            end
            if(pending!==mask) $fatal(1,"Unexpected IRQ sources mask=%h pending=%h",mask,pending);
            $display("DMA_WAIT_IRQ_END cycles=%0d pending=%h",cycles,pending);$fflush();
        end
    endtask
    task automatic acknowledge_sources(input [15:0] mask);
        reg [15:0] pending;reg [31:0] value;
        begin
            if(mask[0]) begin
                bus_read(DMA_BASE+32'h04,value,2'b00);
                if(!value[12] || (value&32'h770)!=0 || !value[1]) $fatal(1,"MM2S completion/error %h",value);
                bus_write(DMA_BASE+32'h04,32'h7000,2'b00);
            end
            if(mask[1]) begin
                bus_read(DMA_BASE+32'h34,value,2'b00);
                if(!value[12] || (value&32'h770)!=0 || !value[1]) $fatal(1,"S2MM completion/error %h",value);
                bus_write(DMA_BASE+32'h34,32'h7000,2'b00);
            end
            repeat(8) @(negedge `PL_CLK);`PS_VIP.read_interrupt(pending);
            if(pending!==16'h0004) $fatal(1,"DMA ACK changed custom IRQ or failed to clear DMA IRQ: %h",pending);
            bus_write(CORE_BASE+32'h70,32'd3,2'b00);
            repeat(8) @(negedge `PL_CLK);`PS_VIP.read_interrupt(pending);
            if(pending!==0) $fatal(1,"Custom W1C IRQ did not deassert %h",pending);
        end
    endtask
    initial begin
        if(!$value$plusargs("RUN=%s",run_dir) || !$value$plusargs("CORE_KIND=%d",core_kind)) $fatal(1,"RUN/CORE_KIND missing");
        if(core_kind!=1 && core_kind!=2) $fatal(1,"Unsupported CORE_KIND");
        $readmemh({run_dir,"/pcm.mem"},pcm,0,671);
        $readmemh({run_dir,"/records.mem"},records,0,155);
        for(n=0;n<672;n=n+1) if($isunknown(pcm[n])) $fatal(1,"Missing PCM vector");
        for(n=0;n<156;n=n+1) if($isunknown(records[n])) $fatal(1,"Missing record vector");
        lengths[0]=0;lengths[1]=511;lengths[2]=512;lengths[3]=671;lengths[4]=672;lengths[5]=672;
        $display("DMA_SYSTEM_BEGIN core=%0d",core_kind);$fflush();
        repeat(20) @(negedge ps_clk);ps_porb=1;ps_srstb=1;
        `PS_VIP.set_debug_level_info(0);`PS_VIP.set_stop_on_error(1);
        repeat(20) @(negedge ps_clk);
        $display("DMA_CLOCK_CHECK_BEGIN time=%0t cycles=%0d",$time,cycles);$fflush();
        @(posedge `PL_CLK);clock_edge=$realtime;@(posedge `PL_CLK);clock_period=$realtime-clock_edge;
        if(clock_period<9.99 || clock_period>10.01) $fatal(1,"FCLK0 period %f",clock_period);
        fabric_reset();
        $display("DMA_IDENTITY_CHECK_BEGIN cycles=%0d",cycles);$fflush();
        expect_register(CORE_BASE,32'h4d464343);expect_register(CORE_BASE+4,32'h00020000);
        expect_register(CORE_BASE+32'h40,32'(core_kind));
        expect_register(CORE_BASE+32'h44,(core_kind==1)?32'h00011828:32'h00030020);
        expect_register(CORE_BASE+32'h60,(core_kind==1)?32'h283fff8a:32'hc556a8e8);
        expect_register(CORE_BASE+32'h64,32'd0);
        $display("DMA_IDENTITY_CHECK_END cycles=%0d",cycles);$fflush();
        // Real DMA sends an invalid one-byte PCM packet; source IRQ routing and
        // error recovery are checked before the valid arithmetic clips.
        prepare_memory(0);reset_dma();
        bus_write(CORE_BASE+32'h74,32'd3,2'b00);bus_write(CORE_BASE+32'h10,32'd672,2'b00);
        bus_write(CORE_BASE+32'h08,32'd1,2'b00);start_mm2s(1);wait_sources(16'h0005);
        bus_read(CORE_BASE+32'h2c,temp,2'b00);if(!temp[2]) $fatal(1,"Malformed DMA packet not rejected");
        expect_register(CORE_BASE+32'h70,32'd2);acknowledge_sources(16'h0005);
        bus_write(CORE_BASE+32'h08,32'd2,2'b00);expect_register(CORE_BASE+32'h0c,32'd0);
        $display("DMA_MALFORMED_PACKET_RECOVERY_PASS");
        $fflush();
        // Cancel a genuine in-flight DMA clip after native PCM consumption.
        // No frame is complete yet. Both DMA channels reset with a deadline,
        // then core ABORT drops its pre-emphasis/framer state before replay.
        reset_dma();prepare_memory(624);
        bus_write(CORE_BASE+32'h10,32'd672,2'b00);arm_s2mm(624);
        bus_write(CORE_BASE+32'h08,32'd1,2'b00);start_mm2s(1344);
        temp=0;partial_wait=0;
        while(temp<173 && partial_wait<4096) begin
            bus_read(CORE_BASE+32'h34,temp,2'b00);partial_wait=partial_wait+1;
        end
        if(temp<173 || temp>=512) $fatal(1,"Partial-input abort window missed: consumed=%0d",temp);
        partial_abort_consumed=temp;
        reset_dma();bus_write(CORE_BASE+32'h08,32'd2,2'b00);
        expect_register(CORE_BASE+32'h0c,32'd0);expect_register(CORE_BASE+32'h34,32'd0);
        expect_register(CORE_BASE+32'h70,32'd0);
        repeat(16) @(negedge `PL_CLK);`PS_VIP.read_interrupt(interrupt_status);
        if(interrupt_status!==0) $fatal(1,"Stale IRQ after DMA reset/core ABORT");
        $display("DMA_PARTIAL_INPUT_ABORT_PASS consumed=%0d",partial_abort_consumed);
        $fflush();
        for(clip=0;clip<6;clip=clip+1) begin
            selected_samples=lengths[clip];selected_frames=(selected_samples<512)?0:1+(selected_samples-512)/160;
            input_beats=0;output_words=0;
            prepare_memory(selected_frames*312);reset_dma();
            bus_write(CORE_BASE+32'h08,32'd4,2'b00);
            bus_write(CORE_BASE+32'h70,32'd3,2'b00);bus_write(CORE_BASE+32'h74,32'd3,2'b00);
            bus_write(CORE_BASE+32'h10,32'(selected_samples),2'b00);
            monitor_clip=1;
            if(selected_frames>0) arm_s2mm(selected_frames*312);
            bus_write(CORE_BASE+32'h08,32'd1,2'b00);
            if(selected_samples>0) start_mm2s(selected_samples*2);
            wait_sources(16'h0004 | ((selected_samples>0)?16'h0001:16'h0000) | ((selected_frames>0)?16'h0002:16'h0000));
            expect_register(CORE_BASE+32'h0c,32'd2);expect_register(CORE_BASE+32'h2c,32'd0);
            expect_register(CORE_BASE+32'h64,32'd0);expect_register(CORE_BASE+32'h70,32'd1);
            expect_register(CORE_BASE+32'h30,32'(selected_samples));expect_register(CORE_BASE+32'h34,32'(selected_samples));
            expect_register(CORE_BASE+32'h38,32'(selected_frames*13));expect_register(CORE_BASE+32'h3c,32'(selected_frames*13));
            expect_register(CORE_BASE+32'h68,32'(selected_samples*2));expect_register(CORE_BASE+32'h6c,32'(selected_frames*312));
            if(selected_frames>0) expect_register(DMA_BASE+32'h58,32'(selected_frames*312));
            if(input_beats!=(selected_samples+1)/2 || output_words!=selected_frames*78) $fatal(1,"Stream transaction count mismatch");
            monitor_clip=0;
            check_memory(selected_frames*312);
            acknowledge_sources(16'h0004 | ((selected_samples>0)?16'h0001:16'h0000) | ((selected_frames>0)?16'h0002:16'h0000));
            irq_mm2s=irq_mm2s+(selected_samples>0);irq_s2mm=irq_s2mm+(selected_frames>0);irq_core=irq_core+1;
            checked_samples=checked_samples+selected_samples;checked_frames=checked_frames+selected_frames;checked_records=checked_records+selected_frames*13;
            $display("DMA_CLIP_PASS clip=%0d samples=%0d frames=%0d records=%0d cycles=%0d",clip,selected_samples,selected_frames,selected_frames*13,cycles);
            $fflush();
        end
        if(checked_samples!=3038 || checked_frames!=6 || checked_records!=78 || input_stalls==0 || held_checks==0)
            $fatal(1,"DMA coverage incomplete");
        fd=$fopen({run_dir,"/dma_simulation.json"},"w");if(!fd) $fatal(1,"Cannot save DMA simulation result");
        $fwrite(fd,"{\n\"status\":\"PASS\",\"clips\":6,\"samples\":3038,\"frames\":6,\"mfcc_records\":78,\"record_word_mismatches\":0,\n");
        $fwrite(fd,"\"core_kind\":%0d,\"clock_period_ns\":%0.3f,\"cycles\":%0d,\"axi_reads\":%0d,\"axi_writes\":%0d,\n",core_kind,clock_period,cycles,reads,writes);
        $fwrite(fd,"\"irq_mm2s_completions\":%0d,\"irq_s2mm_completions\":%0d,\"irq_core_completions\":%0d,\"output_canary_checks\":%0d,\n",irq_mm2s,irq_s2mm,irq_core,canary_checks);
        $fwrite(fd,"\"input_stall_cycles\":%0d,\"output_stall_cycles\":%0d,\"held_stream_checks\":%0d,\"fabric_reset_checks\":%0d,\n",input_stalls,output_stalls,held_checks,reset_checks);
        $fwrite(fd,"\"partial_input_abort_checks\":1,\"partial_abort_consumed\":%0d,\n",partial_abort_consumed);
        $fwrite(fd,"\"malformed_input_error_checks\":1,\"actual_AXI_DMA\":true,\"PS_HP0_DDR_model\":true,\"arm_instructions_executed\":false,\"physical_board_accessed\":false\n}\n");
        $fclose(fd);$display("DMA_SYSTEM_PASS variant=%0d samples=%0d records=%0d cycles=%0d",core_kind,checked_samples,checked_records,cycles);$finish;
    end
endmodule
`undef PS_VIP
`undef PL_CLK
`undef PL_RST_N
`undef DMA_CORE
`undef CORE_RST_N
