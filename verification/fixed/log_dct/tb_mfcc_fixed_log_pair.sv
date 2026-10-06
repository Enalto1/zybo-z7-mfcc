`timescale 1ns/1ps
module tb_mfcc_fixed_log_pair;
    logic clk=0, rst_n=0, valid=0, ready, last=0, error=0;
    logic outvalid, outready=0, outlast, outfloor, outerror;
    logic [59:0] mel=0;
    logic signed [5:0] s=0, outs;
    logic [31:0] frame=0, outframe;
    logic [4:0] idx=0, outidx;
    logic signed [29:0] value;
    logic monitor_enable=0, force_block=1, stalled=0;
    logic [76:0] held;
    integer cycles=0, seen=0, accepted=0, last_accept=-100;
    integer adjacent=0, reorder_wait=0, stall_cycles=0;
    integer fin, fout, protocol, rc_in, rc_out, j, n, is, ifr, ii, il, ie;
    integer ev, ef, ei, es, el, eb, ee;
    logic [59:0] iv;

    always #5 clk=~clk;
    mfcc_fixed_log_pair U_DUT (
        .clk(clk), .rst_n(rst_n), .i_valid(valid), .o_ready(ready),
        .i_mel(mel), .i_bfp_s(s), .i_frame(frame), .i_index(idx),
        .i_last(last), .i_error(error), .o_valid(outvalid), .i_ready(outready),
        .o_log(value), .o_frame(outframe), .o_index(outidx), .o_bfp_s(outs),
        .o_last(outlast), .o_floor(outfloor), .o_error(outerror)
    );

    always @(negedge clk) begin
        cycles=cycles+1;
        outready=!force_block && cycles%17>5 && !(cycles>=500 && cycles<800);
        if(cycles>1000000) $fatal(1,"log pair timeout");
    end
    always @(posedge clk) begin
        if(!rst_n || !monitor_enable) stalled=0;
        else begin
            if(stalled && {outvalid,value,outframe,outidx,outs,outlast,outfloor,outerror}!==held)
                $fatal(1,"log pair output/metadata changed while stalled");
            stalled=outvalid&&!outready;
            held={outvalid,value,outframe,outidx,outs,outlast,outfloor,outerror};
            if(stalled) stall_cycles=stall_cycles+1;
            if(U_DUT.lane1_output_valid && !U_DUT.lane0_output_valid && !U_DUT.output_lane_reg)
                reorder_wait=reorder_wait+1;
            if(valid&&ready) begin
                accepted=accepted+1;
                if(cycles==last_accept+1) adjacent=adjacent+1;
                last_accept=cycles;
            end
            if(outvalid&&outready) begin
                rc_out=$fscanf(fout,"%d %d %d %d %d %d %d\n",ev,ef,ei,es,el,eb,ee);
                if(rc_out!=7 || $signed(value)!=ev || outframe!=ef || outidx!=ei ||
                   $signed(outs)!=es || outlast!=el || outfloor!=eb || outerror!=ee)
                    $fatal(1,"log pair mismatch n=%d got=%d/%d/%d/%d/%d/%d/%d expected=%d/%d/%d/%d/%d/%d/%d",
                           seen,$signed(value),outframe,outidx,$signed(outs),outlast,outfloor,outerror,
                           ev,ef,ei,es,el,eb,ee);
                seen=seen+1;
                if(seen>accepted) $fatal(1,"log pair unexpected output");
            end
        end
    end

    task send_word(input logic [59:0] word_value, input integer word_s, input integer word_frame,
                   input integer word_index, input integer word_last, input integer word_error);
        begin
            mel=word_value; s=word_s; frame=word_frame; idx=word_index;
            last=word_last; error=word_error; valid=1;
            @(posedge clk); while(!ready) @(posedge clk);
            @(negedge clk); valid=0;
        end
    endtask

    initial begin
        fin=$fopen("log_pair_input.txt","r"); fout=$fopen("log_pair_expected.txt","r");
        if(!fin || !fout) $fatal(1,"log pair vector files");
        repeat(4) @(negedge clk); rst_n=1;
        // Reset drops both outstanding long transactions before completion.
        send_word(60'hfffffffffffffff,0,999,0,0,0);
        send_word(60'hffffffffffffffe,0,999,1,0,0);
        repeat(10) @(negedge clk); rst_n=0;
        repeat(3) @(negedge clk); rst_n=1;
        // Reset also drops an output held under backpressure and its younger peer.
        send_word(60'd0,0,998,0,0,0);
        send_word(60'd0,0,998,1,0,0);
        wait(outvalid); repeat(5) @(negedge clk); rst_n=0;
        repeat(3) @(negedge clk); rst_n=1;
        monitor_enable=1; force_block=0;
        rc_in=$fscanf(fin,"%d\n",n);
        if(rc_in!=1 || n%2!=1) $fatal(1,"log pair requires odd-count drain vector");
        for(j=0;j<n;j=j+1) begin
            rc_in=$fscanf(fin,"%d %d %d %d %d %d\n",iv,ifr,ii,is,il,ie);
            if(rc_in!=6) $fatal(1,"log pair input missing");
            send_word(iv,is,ifr,ii,il,ie);
            if(j%9==3) repeat(3) @(negedge clk);
        end
        wait(seen==n); repeat(150) @(negedge clk);
        rc_out=$fscanf(fout,"%d",ev);
        if(rc_out==1 || accepted!=n || seen!=n) $fatal(1,"log pair count/drain");
        if(adjacent==0 || reorder_wait==0 || stall_cycles==0) $fatal(1,"log pair coverage missing");
        protocol=$fopen("protocol.json","w");
        if(!protocol) $fatal(1,"log pair protocol output file");
        $fdisplay(protocol,"{\"status\":\"PASS\",\"transactions\":%0d,\"accepted\":%0d,\"cycles\":%0d,\"adjacent\":%0d,\"reorder_wait\":%0d,\"stalls\":%0d,\"reset_pending\":true,\"reset_held\":true,\"odd_drain\":true}",
                  seen,accepted,cycles,adjacent,reorder_wait,stall_cycles);
        $fclose(protocol);
        $display("PASS LOG_PAIR transactions=%0d cycles=%0d adjacent=%0d reorder_wait=%0d stalls=%0d",
                 seen,cycles,adjacent,reorder_wait,stall_cycles);
        $finish;
    end
endmodule
