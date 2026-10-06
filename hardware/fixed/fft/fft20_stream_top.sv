// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Runtime-N R2^2SDF FFT followed by a two-bank natural-order reorder buffer.
// The baseline output is always consumed; AXI backpressure is added later.
module fft20_stream_top (
    input  wire logic               clk,
    input  wire logic               rst_n,

    input  wire logic [3:0]         cfg_log2_n,
    input  wire logic               cfg_apply,
    output logic                    cfg_ready,
    output logic                    cfg_accept,
    output logic                    cfg_reject,
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
    output logic [9:0]              out_index,
    output logic                    out_last,
    output logic                    out_overflow,

    output logic                    busy,
    output logic                    done,
    output logic                    overflow,
    output logic                    input_frame_error,
    output logic                    reorder_frame_error,
    output logic                    bank_collision_error,
    output logic                    config_consistency_error
);
    logic configured;
    logic config_request_valid;
    logic config_forward;

    logic core_cfg_ready;
    logic core_cfg_error;
    logic [3:0] core_active_log2_n;
    logic core_in_ready;
    logic core_out_valid;
    logic signed [19:0] core_out_re;
    logic signed [19:0] core_out_im;
    logic [9:0] core_out_ordinal;
    logic [9:0] core_out_index_unused;
    logic core_out_last;
    logic core_overflow;
    logic core_busy;
    logic core_input_frame_error;
    logic core_token_out_unused;
    logic core_token_tag_unused;

    logic reorder_cfg_accept;
    logic reorder_cfg_reject;
    logic [3:0] reorder_active_log2_n;
    logic reorder_cfg_ready;
    logic reorder_idle;
    logic reorder_core_ready;

    assign config_request_valid = (cfg_log2_n >= 4'd3) &&
        (cfg_log2_n <= 4'd10);
    assign cfg_ready = core_cfg_ready && reorder_cfg_ready;
    assign config_forward = cfg_apply && cfg_ready && config_request_valid;
    assign active_log2_n = core_active_log2_n;

    // The streaming core has no output stall input.  The verified ping-pong
    // schedule keeps reorder_core_ready asserted for legal continuous frames;
    // a violation is reported by bank_collision_error.
    assign in_ready = configured && core_in_ready;
    assign busy = core_busy || !reorder_idle;
    assign done = out_valid && out_last;
    assign overflow = out_valid && out_overflow;
    assign input_frame_error = core_input_frame_error;

    fft20_fft_r22sdf_core u_core (
        .clk(clk),
        .rst_n(rst_n),
        .cfg_log2_n(cfg_log2_n),
        .cfg_apply(config_forward),
        .cfg_ready(core_cfg_ready),
        .cfg_error(core_cfg_error),
        .active_log2_n(core_active_log2_n),
        .in_valid(in_valid && configured),
        .in_ready(core_in_ready),
        .in_re(in_re),
        .in_im(in_im),
        .in_last(in_last),
        .out_valid(core_out_valid),
        .out_re(core_out_re),
        .out_im(core_out_im),
        .out_ordinal(core_out_ordinal),
        .out_index(core_out_index_unused),
        .out_last(core_out_last),
        .overflow(core_overflow),
        .busy(core_busy),
        .input_frame_error(core_input_frame_error),
        .core_token_out(core_token_out_unused),
        .core_token_tag(core_token_tag_unused)
    );

    fft20_natural_reorder_pingpong #(
        .DATA_W(20),
        .MAX_LOG2_N(10)
    ) u_reorder (
        .clk(clk),
        .rst_n(rst_n),
        .clear(1'b0),
        .cfg_log2_n(cfg_log2_n),
        .cfg_apply(config_forward),
        .cfg_accept(reorder_cfg_accept),
        .cfg_reject(reorder_cfg_reject),
        .cfg_log2_n_active(reorder_active_log2_n),
        .cfg_ready(reorder_cfg_ready),
        .idle(reorder_idle),
        .core_valid(core_out_valid),
        .core_ready(reorder_core_ready),
        .core_re(core_out_re),
        .core_im(core_out_im),
        .core_ordinal(core_out_ordinal),
        .core_last(core_out_last),
        .core_overflow(core_overflow),
        .out_valid(out_valid),
        .out_re(out_re),
        .out_im(out_im),
        .out_index(out_index),
        .out_last(out_last),
        .out_overflow(out_overflow),
        .core_frame_error(reorder_frame_error),
        .bank_collision_error(bank_collision_error)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            configured <= 1'b0;
            cfg_accept <= 1'b0;
            cfg_reject <= 1'b0;
            cfg_error <= 1'b0;
            config_consistency_error <= 1'b0;
        end else begin
            cfg_accept <= config_forward;
            cfg_reject <= cfg_apply && !config_forward;

            if (config_forward) begin
                configured <= 1'b1;
                cfg_error <= 1'b0;
                config_consistency_error <= 1'b0;
            end else if (cfg_apply || core_cfg_error || reorder_cfg_reject) begin
                cfg_error <= 1'b1;
            end

            if (configured &&
                ((core_active_log2_n != reorder_active_log2_n) ||
                 (reorder_cfg_accept && !cfg_accept))) begin
                config_consistency_error <= 1'b1;
                cfg_error <= 1'b1;
            end
        end
    end
endmodule

`default_nettype wire
