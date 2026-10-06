// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// BF Type I arithmetic: direct=D+X and feedback=D-X component-wise.
// OUT_W=IN_W+1 is the no-overflow production configuration.
module fft20_bf_type1 #(
    parameter integer IN_W  = 16,
    parameter integer OUT_W = IN_W + 1
) (
    input  wire logic signed [IN_W-1:0] delay_re,
    input  wire logic signed [IN_W-1:0] delay_im,
    input  wire logic signed [IN_W-1:0] input_re,
    input  wire logic signed [IN_W-1:0] input_im,
    output logic signed [OUT_W-1:0] direct_re,
    output logic signed [OUT_W-1:0] direct_im,
    output logic signed [OUT_W-1:0] feedback_re,
    output logic signed [OUT_W-1:0] feedback_im,
    output logic                    overflow_direct,
    output logic                    overflow_feedback,
    output logic                    overflow
);

    localparam integer CALC_W = IN_W + 1;

    logic signed [CALC_W-1:0] delay_re_ext;
    logic signed [CALC_W-1:0] delay_im_ext;
    logic signed [CALC_W-1:0] input_re_ext;
    logic signed [CALC_W-1:0] input_im_ext;
    logic signed [CALC_W-1:0] direct_re_full;
    logic signed [CALC_W-1:0] direct_im_full;
    logic signed [CALC_W-1:0] feedback_re_full;
    logic signed [CALC_W-1:0] feedback_im_full;
    logic signed [CALC_W-1:0] direct_re_check;
    logic signed [CALC_W-1:0] direct_im_check;
    logic signed [CALC_W-1:0] feedback_re_check;
    logic signed [CALC_W-1:0] feedback_im_check;

    always_comb begin
        delay_re_ext = {delay_re[IN_W-1], delay_re};
        delay_im_ext = {delay_im[IN_W-1], delay_im};
        input_re_ext = {input_re[IN_W-1], input_re};
        input_im_ext = {input_im[IN_W-1], input_im};
        direct_re_full = delay_re_ext + input_re_ext;
        direct_im_full = delay_im_ext + input_im_ext;
        feedback_re_full = delay_re_ext - input_re_ext;
        feedback_im_full = delay_im_ext - input_im_ext;
    end

    assign direct_re = direct_re_full[OUT_W-1:0];
    assign direct_im = direct_im_full[OUT_W-1:0];
    assign feedback_re = feedback_re_full[OUT_W-1:0];
    assign feedback_im = feedback_im_full[OUT_W-1:0];

    generate
        if (CALC_W == OUT_W) begin : g_no_overflow_width_growth
            assign direct_re_check = direct_re;
            assign direct_im_check = direct_im;
            assign feedback_re_check = feedback_re;
            assign feedback_im_check = feedback_im;
        end else begin : g_overflow_width_check
            assign direct_re_check =
                {{(CALC_W-OUT_W){direct_re[OUT_W-1]}}, direct_re};
            assign direct_im_check =
                {{(CALC_W-OUT_W){direct_im[OUT_W-1]}}, direct_im};
            assign feedback_re_check =
                {{(CALC_W-OUT_W){feedback_re[OUT_W-1]}}, feedback_re};
            assign feedback_im_check =
                {{(CALC_W-OUT_W){feedback_im[OUT_W-1]}}, feedback_im};
        end
    endgenerate

    assign overflow_direct = (direct_re_full != direct_re_check) ||
        (direct_im_full != direct_im_check);
    assign overflow_feedback = (feedback_re_full != feedback_re_check) ||
        (feedback_im_full != feedback_im_check);
    assign overflow = overflow_direct | overflow_feedback;

endmodule

`default_nettype wire
