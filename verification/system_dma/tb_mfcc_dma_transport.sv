module tb_mfcc_dma_transport #(parameter integer CORE_KIND=1);
    logic clk=0, rst_n=0;
    always #5 clk=~clk;
    logic [15:0] awaddr=0, araddr=0;
    logic awvalid=0, awready, wvalid=0, wready, bvalid, bready=0;
    logic [31:0] wdata=0, rdata;
    logic [3:0] wstrb=15;
    logic [1:0] bresp, rresp;
    logic arvalid=0, arready, rvalid, rready=0;
    logic [31:0] indata=0, outdata;
    logic [3:0] inkeep=0, outkeep;
    logic inlast=0, invalid=0, inready, outlast, outvalid, outready=0, irq;
    logic start, abort_core, pcmvalid, pcmready=0, pcmlast;
    logic signed [15:0] pcm;
    logic resultvalid=0, resultready, resultlast=0, error=0, metadataerror=0, done=0;
    logic [63:0] payload=0;
    logic [31:0] frame=0;
    logic [3:0] index=0;
    logic signed [7:0] bfp=0;
    logic [11:0] detail=0;
    integer cycles=0, groups=0, expected_samples=0, expected_records=0;
    integer samples_seen=0, records_seen=0, words_seen=0, last_seen=0, starts=0;
    integer input_stalls=0, output_stalls=0, pcm_stalls=0;
    integer prep_count_checks=0, prep_abort_checks=0, prep_error_checks=0;
    integer finish_boundary_checks=0, finish_fault_checks=0;
    logic check_input=1, check_output=1, auto_pcm=1, auto_output=1;
    logic held_out=0, held_pcm=0, held_r=0, held_b=0;
    logic [36:0] saved_out;
    logic [16:0] saved_pcm;
    logic [33:0] saved_r;
    logic [1:0] saved_b;
    logic [31:0] read_value;
    integer k, fd, starts_before, prep_records_before;
    mfcc_dma_transport #(
        .CORE_ID(CORE_KIND==1?32'd1:32'd2),
        .FORMAT(CORE_KIND==1?32'h00011828:32'h00030020),
        .CONTRACT_TAG(CORE_KIND==1?32'h283fff8a:32'hc556a8e8)
    ) U_DUT (
        .s_axi_aclk(clk),.s_axi_aresetn(rst_n),
        .s_axi_awaddr(awaddr),.s_axi_awprot(3'd0),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(3'd0),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
        .s_axis_pcm_tdata(indata),.s_axis_pcm_tkeep(inkeep),.s_axis_pcm_tlast(inlast),
        .s_axis_pcm_tvalid(invalid),.s_axis_pcm_tready(inready),
        .m_axis_result_tdata(outdata),.m_axis_result_tkeep(outkeep),.m_axis_result_tlast(outlast),
        .m_axis_result_tvalid(outvalid),.m_axis_result_tready(outready),.irq(irq),
        .o_clip_start(start),.o_abort(abort_core),.o_pcmvalid(pcmvalid),.i_pcmready(pcmready),
        .o_pcm(pcm),.o_pcm_last(pcmlast),.i_outputvalid(resultvalid),.o_outputready(resultready),
        .i_data64(payload),.i_frame32(frame),.i_index4(index),.i_bfp8(bfp),.i_last(resultlast),
        .i_error(error),.i_metadata_error(metadataerror),.i_core_error_detail(detail),.i_clipdone(done)
    );
    function automatic [15:0] sample_value(input integer n);
        sample_value=16'(n*7919+16'h8000);
    endfunction
    function automatic [63:0] record_value(input integer n);
        if(CORE_KIND==1) record_value=64'hffffff8000000000+64'(n*17011);
        else record_value=(n%13==0)?64'h0000000080000000:(64'h000000003f800000+64'(n*17011));
    endfunction
    function automatic [31:0] expected_word(input integer n,input integer word_index);
        logic [63:0] value;
        integer exponent;
        begin
            value=record_value(n); exponent=(CORE_KIND==1)?(n/13)-7:0;
            case(word_index)
                0: expected_word=value[31:0];
                1: expected_word=value[63:32];
                2: expected_word=32'(n/13);
                3: expected_word=32'(n%13);
                4: expected_word=32'(exponent);
                5: expected_word=(n%13==12)?32'd1:32'd0;
                default: expected_word=0;
            endcase
        end
    endfunction
    always @(negedge clk) begin
        if(auto_pcm) pcmready=(cycles%5!=1)&&(cycles%5!=2);
        if(auto_output) outready=(cycles%11>=4);
    end
    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>250000) $fatal(1,"bounded unit timeout");
        if(!rst_n || abort_core || start) begin
            held_out=0;held_pcm=0;
        end else begin
            if(held_out && (!outvalid || {outdata,outkeep,outlast}!==saved_out)) $fatal(1,"output unstable under stall");
            if(held_pcm && (!pcmvalid || {pcm,pcmlast}!==saved_pcm)) $fatal(1,"PCM unstable under stall");
        end
        if(rst_n) begin
            if(held_r && (!rvalid || {rdata,rresp}!==saved_r)) $fatal(1,"AXI R changed while stalled");
            if(held_b && (!bvalid || bresp!==saved_b)) $fatal(1,"AXI B changed while stalled");
        end
        held_out=outvalid&&!outready; saved_out={outdata,outkeep,outlast};
        held_pcm=pcmvalid&&!pcmready; saved_pcm={pcm,pcmlast};
        held_r=rvalid&&!rready; saved_r={rdata,rresp};
        held_b=bvalid&&!bready; saved_b=bresp;
        if(start) begin
            samples_seen=0;records_seen=0;words_seen=0;last_seen=0;starts=starts+1;
            if(pcmlast !== (expected_samples==0)) $fatal(1,"empty start indication");
        end
        if(rst_n&&pcmvalid&&pcmready) begin
            if(check_input) begin
                if(pcm!==sample_value(samples_seen)) $fatal(1,"PCM order/value sample %0d",samples_seen);
                if(pcmlast !== (samples_seen==expected_samples-1)) $fatal(1,"last marks wrong half sample %0d",samples_seen);
            end
            samples_seen=samples_seen+1;
        end
        if(rst_n&&outvalid&&outready) begin
            if(check_output) begin
                if(outdata!==expected_word(records_seen,words_seen%6)) $fatal(1,"record word mismatch record%0d word%0d got%h",records_seen,words_seen%6,outdata);
                if(outkeep!==4'hf) $fatal(1,"output keep");
                if(outlast !== ((records_seen==expected_records-1)&&(words_seen%6==5))) $fatal(1,"clip TLAST mismatch");
            end
            if(outlast) last_seen=last_seen+1;
            if(words_seen%6==5) records_seen=records_seen+1;
            words_seen=words_seen+1;
        end
        if(invalid&&!inready) input_stalls=input_stalls+1;
        if(outvalid&&!outready) output_stalls=output_stalls+1;
        if(pcmvalid&&!pcmready) pcm_stalls=pcm_stalls+1;
    end
    task automatic idle_clocks(input integer n);
        repeat(n) @(negedge clk);
    endtask
    task automatic write_reg(input [15:0] address,input [31:0] value,input [1:0] response,input integer order=0,input [3:0] strobe=15);
        begin
            @(negedge clk);awaddr=address;wdata=value;wstrb=strobe;
            if(order==0) begin
                awvalid=1;
                do @(posedge clk); while(!awready);
                @(negedge clk);awvalid=0;
                idle_clocks(2);wvalid=1;
                do @(posedge clk); while(!wready);
                @(negedge clk);wvalid=0;
            end else begin
                wvalid=1;
                do @(posedge clk); while(!wready);
                @(negedge clk);wvalid=0;
                idle_clocks(3);awvalid=1;
                do @(posedge clk); while(!awready);
                @(negedge clk);awvalid=0;
            end
            while(!bvalid) @(negedge clk);
            if(bresp!==response) $fatal(1,"write response %h got%h want%h",address,bresp,response);
            idle_clocks(3);bready=1;
            @(negedge clk);bready=0;wstrb=15;
        end
    endtask
    task automatic read_reg(input [15:0] address,input [1:0] response=0);
        begin
            @(negedge clk);araddr=address;arvalid=1;
            do @(posedge clk); while(!arready);
            @(negedge clk);arvalid=0;
            while(!rvalid) @(negedge clk);
            if(rresp!==response) $fatal(1,"read response %h",address);
            read_value=rdata;
            idle_clocks(3);rready=1;
            @(negedge clk);rready=0;
        end
    endtask
    task automatic expect_reg(input [15:0] address,input [31:0] value);
        begin read_reg(address);if(read_value!==value) $fatal(1,"register%h got%h want%h",address,read_value,value);end
    endtask
    task automatic begin_clip(input integer count);
        begin
            expected_samples=count;expected_records=(count<512)?0:(1+(count-512)/160)*13;
            write_reg(16'h10,32'(count),0,1);
            write_reg(16'h08,1,0);
            expect_reg(16'h0c,1);
        end
    endtask
    task automatic beat(input [31:0] data,input [3:0] keep,input logic last);
        begin
            @(negedge clk);indata=data;inkeep=keep;inlast=last;invalid=1;
            do @(posedge clk); while(!inready);
            @(negedge clk);invalid=0;
        end
    endtask
    task automatic feed_clip;
        integer n;
        begin
            for(n=0;n<expected_samples;n=n+2) begin
                beat({sample_value(n+1),sample_value(n)},(n+1==expected_samples)?4'h3:4'hf,(n+2>=expected_samples));
                if(n%10==0) idle_clocks(2);
            end
            while(samples_seen!=expected_samples) @(negedge clk);
            if(inready) $fatal(1,"accepted beyond configured input length");
        end
    endtask
    task automatic result_record(input integer n,input logic bad=0);
        begin
            @(negedge clk);payload=record_value(n);frame=32'(n/13);
            index=bad?4'd3:4'(n%13);bfp=(CORE_KIND==1)?8'(n/13-7):8'd0;resultlast=(n%13==12);resultvalid=1;
            do @(posedge clk); while(!resultready);
            @(negedge clk);resultvalid=0;
        end
    endtask
    task automatic pulse_done;
        begin @(negedge clk);done=1;@(negedge clk);done=0;end
    endtask
    task automatic last_record_with_done;
        integer n;
        begin
            n=expected_records-1;
            @(negedge clk);payload=record_value(n);frame=32'(n/13);
            index=12;bfp=(CORE_KIND==1)?8'(n/13-7):8'd0;resultlast=1;resultvalid=1;
            while(!resultready) @(negedge clk);
            done=1;
            @(posedge clk);
            if(!resultready) $fatal(1,"last record/DONE did not coincide");
            @(negedge clk);resultvalid=0;done=0;
        end
    endtask
    task automatic finish_clip;
        integer n;
        begin
            for(n=0;n<expected_records;n=n+1) result_record(n);
            // Native done arrives while the last record is still serializing.
            pulse_done();
            while(records_seen!=expected_records) @(negedge clk);
            idle_clocks(3);
            expect_reg(16'h0c,2);expect_reg(16'h2c,0);expect_reg(16'h64,0);
            expect_reg(16'h30,32'(expected_samples));expect_reg(16'h34,32'(expected_samples));
            expect_reg(16'h38,32'(expected_records));expect_reg(16'h3c,32'(expected_records));
            expect_reg(16'h68,32'(expected_samples*2));expect_reg(16'h6c,32'(expected_records*24));
            expect_reg(16'h70,1);
            if(!irq || last_seen!==((expected_records==0)?0:1)) $fatal(1,"completion IRQ/clip TLAST");
            write_reg(16'h70,1,0);if(irq) $fatal(1,"DONE ACK");
            expect_reg(16'h0c,2);
        end
    endtask
    task automatic recover;
        begin
            error=0;metadataerror=0;detail=0;resultvalid=0;invalid=0;done=0;
            write_reg(16'h08,2,0);idle_clocks(2);
            expect_reg(16'h0c,0);expect_reg(16'h2c,0);expect_reg(16'h64,0);expect_reg(16'h70,0);
            check_input=1;check_output=1;auto_pcm=1;auto_output=1;
        end
    endtask
    task automatic check_preparation(input integer count);
        integer prep_cycles;
        begin
            expected_samples=count;expected_records=(count<512)?0:(1+(count-512)/160)*13;
            prep_cycles=(count<512)?1:1+(count-512)/160;
            write_reg(16'h10,32'(count),0);
            fork
                write_reg(16'h08,1,0);
                begin
                    // Observe the accepted command edge, before the write
                    // task's deliberately stalled B response has completed.
                    wait(U_DUT.aw_pending_reg && U_DUT.w_pending_reg);
                    @(posedge clk);@(negedge clk);
                    while(!start) begin
                        if(inready||pcmvalid||resultready||outvalid)
                            $fatal(1,"PREP exposed a stream handshake count%0d",count);
                        @(negedge clk);
                    end
                    if(U_DUT.cycles_reg!==64'(prep_cycles))
                        $fatal(1,"PREP busy cycles count%0d got%0d want%0d",count,U_DUT.cycles_reg,prep_cycles);
                    if(U_DUT.expected_records_reg!==32'(expected_records))
                        $fatal(1,"PREP record count N%0d",count);
                    if(inready||pcmvalid||resultready)
                        $fatal(1,"native START cycle exposed stream handshake");
                    prep_count_checks=prep_count_checks+1;
                end
            join
            recover();
        end
    endtask
    initial begin
        idle_clocks(5);rst_n=1;idle_clocks(3);
        expect_reg(0,32'h4d464343);expect_reg(4,32'h00020000);
        expect_reg(16'h40,32'(CORE_KIND));expect_reg(16'h44,CORE_KIND==1?32'h00011828:32'h00030020);
        expect_reg(16'h60,CORE_KIND==1?32'h283fff8a:32'hc556a8e8);
        expect_reg(16'h74,0);write_reg(16'h74,3,0);groups=groups+1;
        begin_clip(0);feed_clip();finish_clip();
        begin_clip(1);feed_clip();finish_clip();
        begin_clip(2);feed_clip();finish_clip();
        begin_clip(3);feed_clip();finish_clip();
        begin_clip(511);feed_clip();finish_clip();
        begin_clip(512);feed_clip();finish_clip();
        begin_clip(671);feed_clip();finish_clip();
        begin_clip(672);feed_clip();finish_clip();
        begin_clip(832);feed_clip();finish_clip();groups=groups+1;
        // Invalid full/even/odd TKEEP and early/missing TLAST stop new ingress.
        for(k=0;k<5;k=k+1) begin
            begin_clip(k==1?1:4);check_input=0;
            case(k)
                0: beat(0,4'h5,0);
                1: beat(0,4'hf,1);
                2: beat(0,4'h3,0);
                3: beat(0,4'hf,1);
                4: begin beat(0,4'hf,0);beat(0,4'hf,0);end
            endcase
            idle_clocks(3);expect_reg(16'h2c,4);expect_reg(16'h70,2);
            if(!irq||inready||pcmvalid||resultready) $fatal(1,"malformed input not stopped");
            recover();
        end
        groups=groups+1;
        // Config/command and removed windows fault; diagnostic IRQ is ACKable.
        write_reg(16'h10,262145,2);expect_reg(16'h2c,2);
        write_reg(16'h70,2,0);if(irq) $fatal(1,"error ACK while detail sticky");
        write_reg(16'h08,4,0);
        read_reg(16'h18,2);expect_reg(16'h2c,1);
        write_reg(16'h08,4,0);write_reg(16'h14,0,2);expect_reg(16'h2c,1);
        write_reg(16'h08,4,0);write_reg(16'h10,2,2,1,3);expect_reg(16'h2c,1);
        write_reg(16'h08,4,0);write_reg(16'h10,262144,0);
        expected_samples=262144;write_reg(16'h08,1,0);
        while(!start) @(negedge clk);
        if(U_DUT.expected_records_reg!==21268) $fatal(1,"maximum record count");
        write_reg(16'h08,1,2);expect_reg(16'h2c,2);recover();groups=groups+1;
        // The exact count and bounded setup time hold at frame boundaries,
        // the development length and the maximum accepted configuration.
        check_preparation(0);check_preparation(1);check_preparation(511);
        check_preparation(512);check_preparation(671);check_preparation(672);
        check_preparation(831);check_preparation(832);
        check_preparation(85920);check_preparation(262144);groups=groups+1;
        // ABORT during lengthy preparation cancels the pending native START.
        // Presented DMA/native data and a stale native done cannot be consumed.
        starts_before=starts;begin_clip(262144);
        @(negedge clk);invalid=1;inkeep=15;inlast=0;resultvalid=1;done=1;
        idle_clocks(8);
        if(starts!=starts_before||inready||pcmvalid||resultready||outvalid)
            $fatal(1,"PREP accepted data or emitted START");
        expect_reg(16'h0c,1);expect_reg(16'h70,0);
        expect_reg(16'h30,0);expect_reg(16'h34,0);expect_reg(16'h38,0);
        recover();idle_clocks(1640);
        if(starts!=starts_before) $fatal(1,"ABORT failed to cancel prepared START");
        begin_clip(3);feed_clip();finish_clip();prep_abort_checks=prep_abort_checks+1;groups=groups+1;
        // CLEAR/config/START remain illegal while PREP is busy. A sticky
        // preparation error prevents delayed START even after IRQ W1C.
        starts_before=starts;begin_clip(262144);
        write_reg(16'h08,4,2);write_reg(16'h10,1,2);write_reg(16'h08,1,2);
        expect_reg(16'h0c,5);expect_reg(16'h2c,2);expect_reg(16'h70,2);
        prep_records_before=U_DUT.expected_records_reg;
        write_reg(16'h70,2,0);if(irq) $fatal(1,"PREP error IRQ ACK");
        idle_clocks(1640);
        if(starts!=starts_before||inready||resultready||
           U_DUT.expected_records_reg!==32'(prep_records_before))
            $fatal(1,"faulted PREP progressed after IRQ ACK");
        recover();begin_clip(0);feed_clip();finish_clip();
        prep_error_checks=prep_error_checks+1;groups=groups+1;
        // Done too early cannot become terminal DONE.
        begin_clip(2);pulse_done();idle_clocks(3);expect_reg(16'h2c,32);expect_reg(16'h0c,5);recover();groups=groups+1;
        // Early DONE must fault promptly even when an already captured record
        // is stalled indefinitely; only successful completion waits for drain.
        begin_clip(512);feed_clip();auto_output=0;outready=0;
        result_record(0);pulse_done();idle_clocks(4);
        expect_reg(16'h2c,32);expect_reg(16'h0c,5);expect_reg(16'h70,2);
        if(!irq||!outvalid||inready||pcmvalid||resultready)
            $fatal(1,"early DONE hidden by stalled output");
        recover();finish_fault_checks=finish_fault_checks+1;groups=groups+1;
        // First native detail survives later values. Output offered before error
        // stays valid and stable under stalls until explicit ABORT.
        begin_clip(512);feed_clip();auto_output=0;outready=0;check_output=0;
        result_record(0);idle_clocks(3);error=1;detail=12'ha55;
        idle_clocks(3);detail=12'h209;idle_clocks(5);
        expect_reg(16'h64,32'ha55);expect_reg(16'h2c,8);expect_reg(16'h70,2);
        if(!outvalid||inready||resultready) $fatal(1,"native error held output policy");
        recover();groups=groups+1;
        // Metadata faults and extra outputs are errors with no fabricated detail.
        begin_clip(512);feed_clip();check_output=0;result_record(0,1);idle_clocks(3);
        expect_reg(16'h2c,16);expect_reg(16'h64,0);recover();groups=groups+1;
        begin_clip(0);check_output=0;result_record(0);idle_clocks(3);
        expect_reg(16'h2c,16);recover();groups=groups+1;
        // A stalled final word must postpone terminal DONE.
        begin_clip(512);feed_clip();
        for(k=0;k<12;k=k+1) result_record(k);
        result_record(12);
        auto_output=0;outready=1;
        while(!(outvalid&&outlast)) @(negedge clk);
        outready=0;pulse_done();idle_clocks(8);
        expect_reg(16'h0c,1);expect_reg(16'h70,0);
        outready=1;idle_clocks(3);expect_reg(16'h0c,2);expect_reg(16'h70,1);
        recover();groups=groups+1;
        // Native DONE on the last record capture commits that record before
        // FINISH validates it. Terminal DONE follows final AXIS acceptance by
        // one clock and no additional input/native record may be accepted.
        begin_clip(512);feed_clip();
        for(k=0;k<12;k=k+1) result_record(k);
        last_record_with_done();
        if(inready||pcmvalid||resultready) $fatal(1,"native DONE did not mask streams");
        auto_output=0;outready=1;
        while(!(outvalid&&outlast)) @(negedge clk);
        outready=0;idle_clocks(5);expect_reg(16'h0c,1);expect_reg(16'h70,0);
        outready=1;@(posedge clk);@(negedge clk);
        if(U_DUT.done_reg||outvalid||irq) $fatal(1,"terminal success before registered final word");
        @(negedge clk);
        if(!U_DUT.done_reg||!irq) $fatal(1,"terminal success missing after registered final word");
        expect_reg(16'h0c,2);expect_reg(16'h2c,0);
        expect_reg(16'h38,13);expect_reg(16'h3c,13);expect_reg(16'h6c,312);
        recover();finish_boundary_checks=finish_boundary_checks+1;groups=groups+1;
        // A native first fault or bad AXI read on FINISH's success edge must
        // win over DONE, retain BUSY and report the appropriate diagnostic.
        begin_clip(0);pulse_done();wait(U_DUT.c_state==U_DUT.S_FINISH);
        @(negedge clk);error=1;detail=12'h209;
        idle_clocks(3);expect_reg(16'h0c,5);expect_reg(16'h2c,8);
        expect_reg(16'h64,32'h209);expect_reg(16'h70,2);recover();
        finish_fault_checks=finish_fault_checks+1;
        begin_clip(0);pulse_done();wait(U_DUT.c_state==U_DUT.S_FINISH);
        @(negedge clk);araddr=16'h18;arvalid=1;
        @(posedge clk);if(!arready) $fatal(1,"FINISH fault read not accepted");
        @(negedge clk);arvalid=0;
        if(!rvalid||rresp!==2) $fatal(1,"FINISH fault read response");
        rready=1;@(negedge clk);rready=0;
        expect_reg(16'h0c,5);expect_reg(16'h2c,1);expect_reg(16'h70,2);
        recover();finish_fault_checks=finish_fault_checks+1;groups=groups+1;
        // Recovery restarts all sample/order state and retains IRQ mask.
        begin_clip(3);feed_clip();finish_clip();expect_reg(16'h74,3);groups=groups+1;
        // W1C and a new event on the same edge: the event wins, even when
        // its diagnostic error bit was already sticky before this access.
        read_reg(16'h18,2);
        fork
            write_reg(16'h70,2,0);
            begin
                wait(U_DUT.aw_pending_reg && U_DUT.w_pending_reg);
                @(negedge clk);araddr=16'h18;arvalid=1;
                do @(posedge clk); while(!arready);
                @(negedge clk);arvalid=0;
                while(!rvalid) @(negedge clk);
                if(rresp!==2) $fatal(1,"coincident bad read response");
                rready=1;@(negedge clk);rready=0;
            end
        join
        expect_reg(16'h70,2);if(!irq) $fatal(1,"new ERROR lost to W1C");
        recover();groups=groups+1;
        begin_clip(0);
        // Capture native DONE, then accept AW/W together on FINISH entry.
        // Their commit is the next edge, exactly the terminal success edge.
        pulse_done();awaddr=16'h70;wdata=1;wstrb=15;awvalid=1;wvalid=1;
        @(posedge clk);
        if(!awready||!wready) $fatal(1,"DONE W1C alignment handshake");
        @(negedge clk);awvalid=0;wvalid=0;
        if(U_DUT.c_state!==U_DUT.S_FINISH||U_DUT.done_reg)
            $fatal(1,"DONE W1C did not align with FINISH");
        @(negedge clk);
        if(!bvalid||bresp!==0||!U_DUT.done_reg||!irq)
            $fatal(1,"new terminal DONE lost to coincident W1C");
        idle_clocks(3);bready=1;@(negedge clk);bready=0;
        expect_reg(16'h70,1);expect_reg(16'h0c,2);
        if(!irq) $fatal(1,"new DONE lost to W1C");groups=groups+1;
        if(input_stalls==0||output_stalls==0||pcm_stalls==0) $fatal(1,"missing stall coverage");
        fd=$fopen($sformatf("dma_transport_core%0d_unit_simulation.json",CORE_KIND),"w");
        $fdisplay(fd,"{\"status\":\"PASS\",\"core_kind\":%0d,\"groups\":%0d,\"cycles\":%0d,\"input_stalls\":%0d,\"output_stalls\":%0d,\"pcm_stalls\":%0d,\"prep_count_checks\":%0d,\"prep_abort_checks\":%0d,\"prep_error_checks\":%0d,\"finish_boundary_checks\":%0d,\"finish_fault_checks\":%0d,\"scope\":\"transport boundary, no arithmetic or AXI DMA IP\"}",CORE_KIND,groups,cycles,input_stalls,output_stalls,pcm_stalls,prep_count_checks,prep_abort_checks,prep_error_checks,finish_boundary_checks,finish_fault_checks);
        $fclose(fd);$display("DMA_TRANSPORT_UNIT_PASS core=%0d groups=%0d cycles=%0d",CORE_KIND,groups,cycles);$finish;
    end
endmodule

module tb_mfcc_dma_transport_fp32;
    tb_mfcc_dma_transport #(.CORE_KIND(2)) U_TEST ();
endmodule

