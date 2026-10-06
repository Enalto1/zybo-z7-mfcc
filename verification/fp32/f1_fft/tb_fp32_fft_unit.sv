`timescale 1ns/1ps
module tb_fp32_fft_unit;
    localparam integer CASES=5;
    logic clk=1'b0;
    logic rst_n=1'b0;
    logic aclken=1'b1;
    logic [23:0] config_data=24'h000001;
    logic config_valid=1'b0;
    logic config_ready;
    logic [63:0] input_data=64'd0;
    logic input_valid=1'b0;
    logic input_ready;
    logic input_last=1'b0;
    logic [63:0] output_data;
    logic [15:0] output_user;
    logic output_valid;
    logic output_ready=1'b0;
    logic output_last;
    logic event_started,event_unexpected,event_missing,event_status_halt,event_input_halt,event_output_halt;
    logic [31:0] input_words[0:CASES*512-1];
    logic [63:0] expected_real[0:CASES*512-1];
    logic [63:0] expected_imag[0:CASES*512-1];
    logic [31:0] lfsr=32'h20261004;
    logic previous_stall=1'b0;
    logic [80:0] previous_payload;
    integer active_case=0;
    integer sent=0;
    integer received=0;
    integer total=0;
    integer violations=0;
    integer output_stalls=0;
    integer input_halts=0;
    integer output_halts=0;
    integer frame_events=0;
    integer config_handshakes=0;
    integer input_handshakes=0;
    integer ce_cycles=0,ce_config_cycles=0,ce_input_cycles=0,ce_compute_cycles=0,ce_output_cycles=0,ce_post_accept_cycles=0;
    integer compute_enabled_cycles=0;
    integer ce_left=0;
    integer ce_reason=0;
    integer ce_input_case=-1,ce_compute_case=-1,ce_output_case=-1,ce_post_accept_case=-1;
    bit ce_config_done=0;
    integer last_stall_case=-1;
    integer stall_left=0;
    integer csv;
    real maximum_error=0.0;
    real sum_square_error=0.0;

    fp32_fft512 DUT (
        .aclk(clk), .aclken(aclken), .aresetn(rst_n),
        .s_axis_config_tdata(config_data), .s_axis_config_tvalid(config_valid), .s_axis_config_tready(config_ready),
        .s_axis_data_tdata(input_data), .s_axis_data_tvalid(input_valid), .s_axis_data_tready(input_ready), .s_axis_data_tlast(input_last),
        .m_axis_data_tdata(output_data), .m_axis_data_tuser(output_user), .m_axis_data_tvalid(output_valid),
        .m_axis_data_tready(output_ready), .m_axis_data_tlast(output_last),
        .event_frame_started(event_started), .event_tlast_unexpected(event_unexpected), .event_tlast_missing(event_missing),
        .event_status_channel_halt(event_status_halt), .event_data_in_channel_halt(event_input_halt), .event_data_out_channel_halt(event_output_halt)
    );
    always #5 clk=!clk;

    function automatic real f32_value(input logic [31:0] bits);
        integer exponent;
        real fraction;
        begin
            if (bits[30:23]==8'hff) $fatal(1,"FFT output NaN/Inf");
            exponent=bits[30:23];
            fraction=$itor(bits[22:0])/8388608.0;
            if (exponent==0) f32_value=fraction*(2.0**(-126));
            else f32_value=(1.0+fraction)*(2.0**(exponent-127));
            if(bits[31]) f32_value=-f32_value;
        end
    endfunction

    always @(negedge clk) begin
        lfsr={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
        if (!rst_n) output_ready=1'b0;
        else if(stall_left>0) begin output_ready=1'b0;stall_left--;end
        else if(output_valid && output_last && last_stall_case!=active_case) begin
            last_stall_case=active_case;stall_left=96;output_ready=1'b0;
        end else output_ready=(lfsr[2:0]!=3'b000 && lfsr[2:0]!=3'b001);
        // CE is an actual vendor port. Drivers/scoreboards must gate every AXIS
        // handshake with it, even when the paused core leaves ready/valid high.
        if (!rst_n) begin aclken=1'b1;ce_left=0;ce_reason=0;end
        else if(ce_left>0) begin aclken=1'b0;ce_left--;end
        else if(!ce_config_done) begin
            ce_config_done=1;aclken=0;ce_left=18;ce_reason=1;
        end else if(sent==193 && ce_input_case!=active_case) begin
            ce_input_case=active_case;aclken=0;ce_left=30;ce_reason=2;
        end else if(sent==512 && received==0 && !output_valid && compute_enabled_cycles>=256 && ce_compute_case!=active_case) begin
            ce_compute_case=active_case;aclken=0;ce_left=52;ce_reason=3;
        end else if(received==18 && ce_post_accept_case!=active_case) begin
            // Pause on the edge immediately after bin17 was accepted: the next
            // valid word can remain exposed with ready high, but is not accepted.
            ce_post_accept_case=active_case;aclken=0;ce_left=36;ce_reason=5;
        end else if(output_valid && output_last && !output_ready && ce_output_case!=active_case) begin
            ce_output_case=active_case;aclken=0;ce_left=22;ce_reason=4;
        end else begin aclken=1'b1;ce_reason=0;end
    end

    always @(posedge clk) begin : CHECK_OUTPUT
        real ar,ai,er,ei,error_value,limit;
        integer offset;
        if(!rst_n) previous_stall=1'b0;
        else begin
            if(aclken && (event_unexpected || event_missing || event_status_halt)) $fatal(1,"FFT input framing/status error");
            if(aclken && event_started) frame_events++;
            if(aclken && event_input_halt) input_halts++;
            if(aclken && event_output_halt) output_halts++;
            if(aclken && config_valid && config_ready) config_handshakes++;
            if(aclken && input_valid && input_ready) input_handshakes++;
            if(aclken && sent==512 && received==0 && !output_valid) compute_enabled_cycles++;
            if(!aclken) begin
                ce_cycles++;
                case(ce_reason)
                    1:ce_config_cycles++;
                    2:ce_input_cycles++;
                    3:ce_compute_cycles++;
                    4:ce_output_cycles++;
                    5:ce_post_accept_cycles++;
                    default:$fatal(1,"Unclassified CE pause");
                endcase
            end
            if(previous_stall && (!output_valid || {output_data,output_user,output_last}!==previous_payload))
                $fatal(1,"FFT AXIS output changed while stalled");
            previous_stall=output_valid&&(!output_ready || !aclken);
            previous_payload={output_data,output_user,output_last};
            if(output_valid&&!output_ready) output_stalls++;
            if(aclken&&output_valid&&output_ready) begin
                if(received>=512 || sent!=512) $fatal(1,"Unexpected output frame size/order");
                if(output_user[8:0]!==received || output_user[15:9]!==7'd0) $fatal(1,"XK_INDEX not natural order");
                if(output_last !== (received==511)) $fatal(1,"Output TLAST not on bin511");
                offset=active_case*512+received;
                ar=f32_value(output_data[31:0]);ai=f32_value(output_data[63:32]);
                er=$bitstoreal(expected_real[offset]);ei=$bitstoreal(expected_imag[offset]);
                error_value=$sqrt((ar-er)*(ar-er)+(ai-ei)*(ai-ei));
                limit=2.0e-5+2.0e-5*$sqrt(er*er+ei*ei);
                if(error_value>limit) violations++;
                if(error_value>maximum_error) maximum_error=error_value;
                sum_square_error=sum_square_error+error_value*error_value;
                $fwrite(csv,"%0d,%0d,%08h,%08h,%.17g,%.17g,%.17g,%.17g,%.17g,%.17g\n",active_case,received,
                    output_data[31:0],output_data[63:32],ar,ai,er,ei,error_value,limit);
                received++;total++;
            end
        end
    end

    initial begin : DRIVER
        integer item,scenario;
        $readmemh("inputs.mem",input_words);
        $readmemh("expected_real.mem",expected_real);
        $readmemh("expected_imag.mem",expected_imag);
        csv=$fopen("fft_results.csv","w");
        if(csv==0)$fatal(1,"Cannot open FFT result CSV");
        $fwrite(csv,"case,bin,real_hex,imag_hex,actual_real,actual_imag,expected_real,expected_imag,absolute_error,tolerance\n");
        repeat(8) @(negedge clk);
        rst_n=1'b1;
        repeat(8) @(negedge clk);
        config_valid=1'b1;
        do @(posedge clk);while(!(aclken&&config_ready));
        @(negedge clk);config_valid=1'b0;
        for(scenario=0;scenario<CASES;scenario++)begin
            active_case=scenario;sent=0;received=0;compute_enabled_cycles=0;
            for(item=0;item<512;item++)begin
                if(scenario%2==1 && item%17==0)repeat(3)@(negedge clk);
                input_data={32'd0,input_words[scenario*512+item]};input_valid=1'b1;input_last=(item==511);
                do @(posedge clk);while(!(aclken&&input_ready));
                sent++;
                @(negedge clk);input_valid=1'b0;input_last=1'b0;
            end
            wait(received==512);
            repeat(10)@(negedge clk);
            $display("FFT_UNIT_CASE_COMPLETE case=%0d outputs=%0d",scenario,received);
        end
        $fclose(csv);
        if(total!=CASES*512 || input_handshakes!=CASES*512 || config_handshakes!=1 || frame_events!=CASES || output_stalls<100)
            $fatal(1,"FFT unit coverage incomplete");
        if(ce_config_cycles!=19 || ce_input_cycles!=CASES*31 || ce_compute_cycles!=CASES*53 || ce_output_cycles!=CASES*23 || ce_post_accept_cycles!=CASES*37)
            $fatal(1,"FFT CE coverage incomplete config=%0d input=%0d compute=%0d output=%0d post_accept=%0d",ce_config_cycles,ce_input_cycles,ce_compute_cycles,ce_output_cycles,ce_post_accept_cycles);
        $display("FFT_UNIT_METRICS words=%0d violations=%0d max_abs=%.17g rmse=%.17g output_stalls=%0d input_halts=%0d output_halts=%0d",
            total,violations,maximum_error,$sqrt(sum_square_error/total),output_stalls,input_halts,output_halts);
        if(violations!=0)$fatal(1,"FFT numerical tolerance failed");
        $display("FFT_CE_METRICS cycles=%0d config=%0d input=%0d compute=%0d output=%0d post_accept=%0d config_handshakes=%0d input_handshakes=%0d output_handshakes=%0d",
            ce_cycles,ce_config_cycles,ce_input_cycles,ce_compute_cycles,ce_output_cycles,ce_post_accept_cycles,config_handshakes,input_handshakes,total);
        $display("FFT_UNIT_PASS gain_sign_natural_index_tlast_backpressure_ce_verified=1");
        $finish;
    end
    initial begin #10000000;$fatal(1,"FFT unit watchdog timeout");end
endmodule
