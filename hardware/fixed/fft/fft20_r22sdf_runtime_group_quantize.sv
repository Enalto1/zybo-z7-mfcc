// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Runtime full-group twiddle and two-bit convergent group scaling.
// An address/data/meta pre-stage and two following pipeline cuts isolate
// address generation, synchronous ROM lookup, DSP accumulation, and
// requantization. D=1 is the terminal L=4 group and bypasses only the
// numerical multiplier; it retains the same token latency.
module fft20_r22sdf_runtime_group_quantize #(
    parameter ROM_FILE = "twiddle_1024_w16.mem"
) (
    input  wire logic               clk,
    input  wire logic               rst_n,
    input  wire logic               clear,
    input  wire logic               in_token_valid,
    input  wire logic               in_tag,
    input  wire logic signed [21:0] in_re,
    input  wire logic signed [21:0] in_im,
    input  wire logic               in_overflow,
    input  wire logic [9:0]         in_local_ordinal,
    input  wire logic [3:0]         delay_log2,
    output logic                    out_token_valid,
    output logic                    out_tag,
    output logic signed [19:0]      out_re,
    output logic signed [19:0]      out_im,
    output logic                    out_overflow
);
    localparam integer PRODUCT_W = 38;
    localparam integer ACCUM_W = 39;

    logic [9:0] rom_address_comb;
    logic [9:0] rom_address_pre;
    logic token_pre;
    logic tag_pre;
    logic overflow_pre;
    logic bypass_pre;
    logic signed [21:0] data_re_pre;
    logic signed [21:0] data_im_pre;

    logic token_s0;
    logic tag_s0;
    logic overflow_s0;
    logic bypass_s0;
    logic signed [21:0] data_re_s0;
    logic signed [21:0] data_im_s0;
    logic signed [21:0] bypass_re_s0;
    logic signed [21:0] bypass_im_s0;
    logic signed [15:0] twiddle_re_s0;
    logic signed [15:0] twiddle_im_s0;

    logic signed [PRODUCT_W-1:0] product_rr;
    logic signed [PRODUCT_W-1:0] product_ii;
    logic signed [PRODUCT_W-1:0] product_ri;
    logic signed [PRODUCT_W-1:0] product_ir;
    logic signed [ACCUM_W-1:0] full_re_comb;
    logic signed [ACCUM_W-1:0] full_im_comb;

    logic token_s1;
    logic tag_s1;
    logic overflow_s1;
    logic bypass_s1;
    logic signed [21:0] bypass_re_s1;
    logic signed [21:0] bypass_im_s1;
    logic signed [ACCUM_W-1:0] full_re_s1;
    logic signed [ACCUM_W-1:0] full_im_s1;

    logic signed [21:0] multiplied_re;
    logic signed [21:0] multiplied_im;
    logic signed [21:0] scaled_input_re_s2;
    logic signed [21:0] scaled_input_im_s2;
    logic overflow_s2;
    logic multiply_overflow;
    logic multiply_overflow_re;
    logic multiply_overflow_im;
    logic shift_overflow_re;
    logic shift_overflow_im;

    fft20_r22sdf_runtime_twiddle_addr_gen u_address (
        .delay_log2(delay_log2), .local_ordinal(in_local_ordinal),
        .rom_address(rom_address_comb));
    fft20_twiddle_rom_sync #(.ROM_FILE(ROM_FILE)) u_rom (
        .clk(clk), .address(rom_address_pre), .twiddle_re(twiddle_re_s0),
        .twiddle_im(twiddle_im_s0));

    always_comb begin
        product_rr = $signed(data_re_s0) * $signed(twiddle_re_s0);
        product_ii = $signed(data_im_s0) * $signed(twiddle_im_s0);
        product_ri = $signed(data_re_s0) * $signed(twiddle_im_s0);
        product_ir = $signed(data_im_s0) * $signed(twiddle_re_s0);
        full_re_comb = $signed({product_rr[PRODUCT_W-1], product_rr}) -
            $signed({product_ii[PRODUCT_W-1], product_ii});
        full_im_comb = $signed({product_ri[PRODUCT_W-1], product_ri}) +
            $signed({product_ir[PRODUCT_W-1], product_ir});
    end

    fft20_fixed_round_shift #(
        .IN_W(ACCUM_W), .OUT_W(22), .SHIFT(15),
        .ROUND_CONVERGENT(1)
    ) u_multiply_round_re (
        .in_data(full_re_s1), .out_data(multiplied_re),
        .overflow(multiply_overflow_re));
    fft20_fixed_round_shift #(
        .IN_W(ACCUM_W), .OUT_W(22), .SHIFT(15),
        .ROUND_CONVERGENT(1)
    ) u_multiply_round_im (
        .in_data(full_im_s1), .out_data(multiplied_im),
        .overflow(multiply_overflow_im));
    assign multiply_overflow = multiply_overflow_re |
        multiply_overflow_im;

    always_ff @(posedge clk) begin
        if (!rst_n || clear) begin
            token_pre <= 1'b0;
            tag_pre <= 1'b0;
            token_s0 <= 1'b0;
            tag_s0 <= 1'b0;
            token_s1 <= 1'b0;
            tag_s1 <= 1'b0;
            out_token_valid <= 1'b0;
            out_tag <= 1'b0;
        end else begin
            // Register the ROM address before the BRAM input. The matching
            // complex sample and metadata enter the same pre-stage.
            rom_address_pre <= rom_address_comb;
            token_pre <= in_token_valid;
            tag_pre <= in_token_valid & in_tag;
            overflow_pre <= in_overflow;
            bypass_pre <= (delay_log2 == 4'd0);
            data_re_pre <= in_re;
            data_im_pre <= in_im;

            // The synchronous ROM output for rom_address_pre becomes valid
            // on this edge and is aligned with the pre-stage sample in s0.
            token_s0 <= token_pre;
            tag_s0 <= tag_pre;
            overflow_s0 <= overflow_pre;
            bypass_s0 <= bypass_pre;
            data_re_s0 <= data_re_pre;
            data_im_s0 <= data_im_pre;
            bypass_re_s0 <= data_re_pre;
            bypass_im_s0 <= data_im_pre;
            token_s1 <= token_s0;
            tag_s1 <= tag_s0;
            overflow_s1 <= overflow_s0;
            bypass_s1 <= bypass_s0;
            bypass_re_s1 <= bypass_re_s0;
            bypass_im_s1 <= bypass_im_s0;
            full_re_s1 <= full_re_comb;
            full_im_s1 <= full_im_comb;

            out_token_valid <= token_s1;
            out_tag <= tag_s1;
            if (bypass_s1) begin
                scaled_input_re_s2 <= bypass_re_s1;
                scaled_input_im_s2 <= bypass_im_s1;
            end else begin
                scaled_input_re_s2 <= multiplied_re;
                scaled_input_im_s2 <= multiplied_im;
            end
            overflow_s2 <= overflow_s1 |
                ((!bypass_s1) && multiply_overflow);
        end
    end

    fft20_fixed_round_shift #(
        .IN_W(22), .OUT_W(20), .SHIFT(2), .ROUND_CONVERGENT(1)
    ) u_shift_re (
        .in_data(scaled_input_re_s2), .out_data(out_re),
        .overflow(shift_overflow_re));
    fft20_fixed_round_shift #(
        .IN_W(22), .OUT_W(20), .SHIFT(2), .ROUND_CONVERGENT(1)
    ) u_shift_im (
        .in_data(scaled_input_im_s2), .out_data(out_im),
        .overflow(shift_overflow_im));

    assign out_overflow = overflow_s2 |
        shift_overflow_re | shift_overflow_im;
endmodule

`default_nettype wire
