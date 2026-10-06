// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// BF Type II arithmetic. rotate_minus_j maps X=(re,im) to (im,-re)
// before D+X/D-X; widening precedes negation of the minimum input value.
module fft20_bf_type2 #(
    parameter integer IN_W  = 17,
    parameter integer OUT_W = IN_W + 1
) (
    input  wire logic signed [IN_W-1:0] delay_re,
    input  wire logic signed [IN_W-1:0] delay_im,
    input  wire logic signed [IN_W-1:0] input_re,
    input  wire logic signed [IN_W-1:0] input_im,
    input  wire logic                    rotate_minus_j,
    output logic signed [OUT_W-1:0] direct_re,
    output logic signed [OUT_W-1:0] direct_im,
    output logic signed [OUT_W-1:0] feedback_re,
    output logic signed [OUT_W-1:0] feedback_im,
    output logic                    overflow_direct,
    output logic                    overflow_feedback,
    output logic                    overflow
);

    localparam integer CALC_W = IN_W + 2;

    logic signed [CALC_W-1:0] delay_re_ext;
    logic signed [CALC_W-1:0] delay_im_ext;
    logic signed [CALC_W-1:0] input_re_ext;
    logic signed [CALC_W-1:0] input_im_ext;
    logic signed [CALC_W-1:0] rotated_re;
    logic signed [CALC_W-1:0] rotated_im;
    logic signed [CALC_W-1:0] direct_re_full;
    logic signed [CALC_W-1:0] direct_im_full;
    logic signed [CALC_W-1:0] feedback_re_full;
    logic signed [CALC_W-1:0] feedback_im_full;
    logic signed [CALC_W-1:0] direct_re_check;
    logic signed [CALC_W-1:0] direct_im_check;
    logic signed [CALC_W-1:0] feedback_re_check;
    logic signed [CALC_W-1:0] feedback_im_check;

    always_comb begin
        delay_re_ext = {{2{delay_re[IN_W-1]}}, delay_re};
        delay_im_ext = {{2{delay_im[IN_W-1]}}, delay_im};
        input_re_ext = {{2{input_re[IN_W-1]}}, input_re};
        input_im_ext = {{2{input_im[IN_W-1]}}, input_im};
        if (rotate_minus_j) begin
            rotated_re = input_im_ext;
            rotated_im = -input_re_ext;
        end else begin
            rotated_re = input_re_ext;
            rotated_im = input_im_ext;
        end
        direct_re_full = delay_re_ext + rotated_re;
        direct_im_full = delay_im_ext + rotated_im;
        feedback_re_full = delay_re_ext - rotated_re;
        feedback_im_full = delay_im_ext - rotated_im;
    end

    assign direct_re = direct_re_full[OUT_W-1:0];
    assign direct_im = direct_im_full[OUT_W-1:0];
    assign feedback_re = feedback_re_full[OUT_W-1:0];
    assign feedback_im = feedback_im_full[OUT_W-1:0];

    assign direct_re_check =
        {{(CALC_W-OUT_W){direct_re[OUT_W-1]}}, direct_re};
    assign direct_im_check =
        {{(CALC_W-OUT_W){direct_im[OUT_W-1]}}, direct_im};
    assign feedback_re_check =
        {{(CALC_W-OUT_W){feedback_re[OUT_W-1]}}, feedback_re};
    assign feedback_im_check =
        {{(CALC_W-OUT_W){feedback_im[OUT_W-1]}}, feedback_im};

    assign overflow_direct = (direct_re_full != direct_re_check) ||
        (direct_im_full != direct_im_check);
    assign overflow_feedback = (feedback_re_full != feedback_re_check) ||
        (feedback_im_full != feedback_im_check);
    assign overflow = overflow_direct | overflow_feedback;

endmodule

`default_nettype wire
