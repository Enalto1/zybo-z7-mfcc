// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Two fixed-parity physical logical stages.  Runtime N activates a prefix:
// neither stage, Type-I only (odd log2 N), or the complete Type-I/II pair.
module fft20_r22sdf_runtime_group #(
    parameter integer BASE_STAGE = 0,
    parameter integer MAX_DELAY_I = 512,
    parameter integer MAX_DELAY_II = 256,
    parameter ROM_FILE = "twiddle_1024_w16.mem"
) (
    input  wire logic               clk,
    input  wire logic               rst_n,
    input  wire logic               clear,
    input  wire logic [3:0]         cfg_log2_n,
    input  wire logic               in_token_valid,
    input  wire logic               in_tag,
    input  wire logic signed [19:0] in_re,
    input  wire logic signed [19:0] in_im,
    input  wire logic               in_overflow,
    output logic                    out_token_valid,
    output logic                    out_tag,
    output logic signed [19:0]      out_re,
    output logic signed [19:0]      out_im,
    output logic                    out_overflow
);
    localparam [3:0] FIRST_STAGE = BASE_STAGE;
    localparam [3:0] SECOND_STAGE = BASE_STAGE + 1;

    logic stage_i_active;
    logic stage_ii_active;
    logic [3:0] delay_i_log2;
    logic [3:0] delay_ii_log2;
    logic i_token, i_tag, i_overflow;
    logic signed [20:0] i_re, i_im;
    logic [9:0] i_ordinal;
    logic ii_token, ii_tag, ii_overflow;
    logic signed [21:0] ii_re, ii_im;
    logic [9:0] ii_ordinal;
    logic signed [19:0] full_re, full_im;
    logic full_token, full_tag, full_overflow;
    logic signed [19:0] partial_re, partial_im;
    logic partial_overflow_re, partial_overflow_im;

    always_comb begin
        stage_i_active = cfg_log2_n > FIRST_STAGE;
        stage_ii_active = cfg_log2_n > SECOND_STAGE;
        if (stage_i_active)
            delay_i_log2 = cfg_log2_n - FIRST_STAGE - 4'd1;
        else
            delay_i_log2 = 4'd0;
        if (stage_ii_active)
            delay_ii_log2 = cfg_log2_n - SECOND_STAGE - 4'd1;
        else
            delay_ii_log2 = 4'd0;
    end

    fft20_r22sdf_runtime_stage #(
        .IN_W(20), .OUT_W(21), .MAX_DELAY(MAX_DELAY_I), .TYPE_II(0)
    ) u_stage_i (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .delay_log2(delay_i_log2),
        .in_token_valid(in_token_valid & stage_i_active), .in_tag(in_tag),
        .in_re(in_re), .in_im(in_im), .in_overflow(in_overflow),
        .out_token_valid(i_token), .out_tag(i_tag),
        .out_re(i_re), .out_im(i_im), .out_overflow(i_overflow),
        .out_local_ordinal(i_ordinal));

    fft20_r22sdf_runtime_stage #(
        .IN_W(21), .OUT_W(22), .MAX_DELAY(MAX_DELAY_II), .TYPE_II(1)
    ) u_stage_ii (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .delay_log2(delay_ii_log2),
        .in_token_valid(i_token & stage_ii_active), .in_tag(i_tag),
        .in_re(i_re), .in_im(i_im), .in_overflow(i_overflow),
        .out_token_valid(ii_token), .out_tag(ii_tag),
        .out_re(ii_re), .out_im(ii_im), .out_overflow(ii_overflow),
        .out_local_ordinal(ii_ordinal));

    fft20_r22sdf_runtime_group_quantize #(.ROM_FILE(ROM_FILE)) u_full_quantize (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .in_token_valid(ii_token), .in_tag(ii_tag),
        .in_re(ii_re), .in_im(ii_im), .in_overflow(ii_overflow),
        .in_local_ordinal(ii_ordinal), .delay_log2(delay_ii_log2),
        .out_token_valid(full_token), .out_tag(full_tag),
        .out_re(full_re), .out_im(full_im),
        .out_overflow(full_overflow));

    // A trailing unpaired Type-I stage has one-bit growth and one-bit shift.
    fft20_fixed_round_shift #(
        .IN_W(21), .OUT_W(20), .SHIFT(1), .ROUND_CONVERGENT(1)
    ) u_partial_shift_re (
        .in_data(i_re), .out_data(partial_re),
        .overflow(partial_overflow_re));
    fft20_fixed_round_shift #(
        .IN_W(21), .OUT_W(20), .SHIFT(1), .ROUND_CONVERGENT(1)
    ) u_partial_shift_im (
        .in_data(i_im), .out_data(partial_im),
        .overflow(partial_overflow_im));

    always_comb begin
        if (!stage_i_active) begin
            out_token_valid = in_token_valid;
            out_tag = in_tag;
            out_re = in_re;
            out_im = in_im;
            out_overflow = in_overflow;
        end else if (!stage_ii_active) begin
            out_token_valid = i_token;
            out_tag = i_tag;
            out_re = partial_re;
            out_im = partial_im;
            out_overflow = i_overflow | partial_overflow_re |
                partial_overflow_im;
        end else begin
            out_token_valid = full_token;
            out_tag = full_tag;
            out_re = full_re;
            out_im = full_im;
            out_overflow = full_overflow;
        end
    end
endmodule

`default_nettype wire
