// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Runtime-N forward DIF R2^2SDF core. Input is natural order and the tagged
// output stream is bit-reversed.  The core accepts only cfg_log2_n=3..10.
module fft20_fft_r22sdf_core (
    input  wire logic               clk,
    input  wire logic               rst_n,
    input  wire logic [3:0]         cfg_log2_n,
    input  wire logic               cfg_apply,
    output logic                    cfg_ready,
    output logic                    cfg_error,
    output logic [3:0]              active_log2_n,
    input  wire logic               in_valid,
    output logic                    in_ready,
    input  wire logic signed [19:0] in_re,
    input  wire logic signed [19:0] in_im,
    input  wire logic               in_last,
    output logic                    out_valid,
    output logic signed [19:0]      out_re,
    output logic signed [19:0]      out_im,
    output logic [9:0]              out_ordinal,
    output logic [9:0]              out_index,
    output logic                    out_last,
    output logic                    overflow,
    output logic                    busy,
    output logic                    input_frame_error,
    output logic                    core_token_out,
    output logic                    core_token_tag
);
    localparam logic [2:0] ST_IDLE = 3'd0;
    localparam logic [2:0] ST_IN_FRAME = 3'd1;
    localparam logic [2:0] ST_BETWEEN = 3'd2;
    localparam logic [2:0] ST_DRAIN = 3'd3;
    localparam logic [2:0] ST_WAIT = 3'd4;
    localparam logic [2:0] ST_CLEAR = 3'd5;

    logic [2:0] state;
    logic cfg_accept;
    logic stage_clear;
    logic scheduled_token;
    logic scheduled_tag;
    logic signed [19:0] scheduled_re;
    logic signed [19:0] scheduled_im;
    logic [10:0] frame_length;
    logic [9:0] frame_last_count;
    logic [9:0] input_count;
    logic [10:0] dummy_count;
    logic [15:0] pending_frames;
    logic accepted_input;
    logic input_frame_complete;
    logic output_frame_complete;

    logic group_token [0:5];
    logic group_tag [0:5];
    logic signed [19:0] group_re [0:5];
    logic signed [19:0] group_im [0:5];
    logic group_overflow [0:5];
    logic [9:0] output_count;
    logic output_frame_overflow;

    function automatic [9:0] reverse_index;
        input [9:0] value;
        input [3:0] width;
        begin
            case (width)
                4'd3: reverse_index = {7'd0,value[0],value[1],value[2]};
                4'd4: reverse_index = {6'd0,value[0],value[1],value[2],value[3]};
                4'd5: reverse_index = {5'd0,value[0],value[1],value[2],value[3],value[4]};
                4'd6: reverse_index = {4'd0,value[0],value[1],value[2],value[3],value[4],value[5]};
                4'd7: reverse_index = {3'd0,value[0],value[1],value[2],value[3],value[4],value[5],value[6]};
                4'd8: reverse_index = {2'd0,value[0],value[1],value[2],value[3],value[4],value[5],value[6],value[7]};
                4'd9: reverse_index = {1'd0,value[0],value[1],value[2],value[3],value[4],value[5],value[6],value[7],value[8]};
                default: reverse_index = {value[0],value[1],value[2],value[3],value[4],value[5],value[6],value[7],value[8],value[9]};
            endcase
        end
    endfunction

    always_comb begin
        frame_length = 11'd1 << active_log2_n;
        frame_last_count = frame_length[9:0] - 10'd1;
        cfg_ready = (state == ST_IDLE);
        busy = (state != ST_IDLE);
        in_ready = ((state == ST_IDLE) || (state == ST_IN_FRAME) ||
            (state == ST_BETWEEN)) && !((state == ST_IDLE) && cfg_apply);
        cfg_accept = cfg_apply && (state == ST_IDLE) &&
            (cfg_log2_n >= 4'd3) && (cfg_log2_n <= 4'd10);
        stage_clear = (state == ST_CLEAR) || cfg_accept;

        scheduled_token = 1'b0;
        scheduled_tag = 1'b0;
        scheduled_re = '0;
        scheduled_im = '0;
        if (((state == ST_IDLE) || (state == ST_IN_FRAME)) &&
            in_valid && in_ready) begin
            scheduled_token = 1'b1;
            scheduled_tag = 1'b1;
            scheduled_re = in_re;
            scheduled_im = in_im;
        end else if (state == ST_BETWEEN) begin
            scheduled_token = 1'b1;
            if (in_valid) begin
                scheduled_tag = 1'b1;
                scheduled_re = in_re;
                scheduled_im = in_im;
            end
        end else if (state == ST_DRAIN) begin
            scheduled_token = 1'b1;
        end
    end

    assign accepted_input = in_valid && in_ready;
    assign input_frame_complete = accepted_input &&
        (input_count == frame_last_count);

    assign group_token[0] = scheduled_token;
    assign group_tag[0] = scheduled_tag;
    assign group_re[0] = scheduled_re;
    assign group_im[0] = scheduled_im;
    assign group_overflow[0] = 1'b0;

    generate
        genvar group_index;
        for (group_index = 0; group_index < 5; group_index = group_index + 1) begin : g_groups
            localparam integer GROUP_BASE = 2*group_index;
            localparam integer GROUP_MAX_I = 512 >> (2*group_index);
            localparam integer GROUP_MAX_II = 256 >> (2*group_index);
            fft20_r22sdf_runtime_group #(
                .BASE_STAGE(GROUP_BASE),
                .MAX_DELAY_I(GROUP_MAX_I),
                .MAX_DELAY_II(GROUP_MAX_II)
            ) u_group (
                .clk(clk), .rst_n(rst_n), .clear(stage_clear),
                .cfg_log2_n(active_log2_n),
                .in_token_valid(group_token[group_index]),
                .in_tag(group_tag[group_index]),
                .in_re(group_re[group_index]), .in_im(group_im[group_index]),
                .in_overflow(group_overflow[group_index]),
                .out_token_valid(group_token[group_index+1]),
                .out_tag(group_tag[group_index+1]),
                .out_re(group_re[group_index+1]),
                .out_im(group_im[group_index+1]),
                .out_overflow(group_overflow[group_index+1]));
        end
    endgenerate

    assign core_token_out = group_token[5];
    assign core_token_tag = group_tag[5];
    assign out_valid = group_token[5] && group_tag[5];
    assign out_re = group_re[5];
    assign out_im = group_im[5];
    assign out_ordinal = output_count;
    assign out_index = reverse_index(output_count, active_log2_n);
    assign out_last = out_valid && (output_count == frame_last_count);
    assign overflow = out_valid &&
        (output_frame_overflow || group_overflow[5]);
    assign output_frame_complete = out_valid && out_last;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= ST_IDLE;
            active_log2_n <= 4'd10;
            cfg_error <= 1'b0;
            input_frame_error <= 1'b0;
            input_count <= '0;
            dummy_count <= '0;
            pending_frames <= '0;
            output_count <= '0;
            output_frame_overflow <= 1'b0;
        end else begin
            cfg_error <= 1'b0;

            if (cfg_apply && !cfg_accept)
                cfg_error <= 1'b1;

            if (cfg_accept) begin
                active_log2_n <= cfg_log2_n;
                state <= ST_IDLE;
                input_frame_error <= 1'b0;
                input_count <= '0;
                dummy_count <= '0;
                pending_frames <= '0;
                output_count <= '0;
                output_frame_overflow <= 1'b0;
            end else begin
                case (state)
                    ST_IDLE: begin
                        if (accepted_input) begin
                            if (in_last != (input_count == frame_last_count))
                                input_frame_error <= 1'b1;
                            input_count <= input_count + 10'd1;
                            state <= ST_IN_FRAME;
                        end
                    end
                    ST_IN_FRAME: begin
                        if (accepted_input) begin
                            if (in_last != (input_count == frame_last_count))
                                input_frame_error <= 1'b1;
                            if (input_count == frame_last_count) begin
                                input_count <= '0;
                                state <= ST_BETWEEN;
                            end else begin
                                input_count <= input_count + 10'd1;
                            end
                        end
                    end
                    ST_BETWEEN: begin
                        if (in_valid) begin
                            if (in_last)
                                input_frame_error <= 1'b1;
                            input_count <= 10'd1;
                            state <= ST_IN_FRAME;
                        end else begin
                            dummy_count <= 11'd1;
                            state <= ST_DRAIN;
                        end
                    end
                    ST_DRAIN: begin
                        if (dummy_count == (frame_length - 11'd1)) begin
                            dummy_count <= '0;
                            state <= ST_WAIT;
                        end else begin
                            dummy_count <= dummy_count + 11'd1;
                        end
                    end
                    ST_WAIT: begin
                        if (output_frame_complete && (pending_frames == 16'd1))
                            state <= ST_CLEAR;
                    end
                    ST_CLEAR: begin
                        state <= ST_IDLE;
                        input_count <= '0;
                        dummy_count <= '0;
                        pending_frames <= '0;
                        output_count <= '0;
                        output_frame_overflow <= 1'b0;
                    end
                    default: state <= ST_CLEAR;
                endcase

                case ({input_frame_complete, output_frame_complete})
                    2'b10: pending_frames <= pending_frames + 16'd1;
                    2'b01: pending_frames <= pending_frames - 16'd1;
                    default: pending_frames <= pending_frames;
                endcase

                if (out_valid) begin
                    if (output_count == frame_last_count) begin
                        output_count <= '0;
                        output_frame_overflow <= 1'b0;
                    end else begin
                        output_count <= output_count + 10'd1;
                        output_frame_overflow <= output_frame_overflow |
                            group_overflow[5];
                    end
                end
            end
        end
    end
endmodule

`default_nettype wire
