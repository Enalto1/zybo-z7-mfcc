// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Runtime-delay logical SDF stage. token_valid advances arithmetic state;
// tag marks whether that token belongs to a user frame.  The tag and the
// sample overflow bit follow the same feedback path as the complex sample.
module fft20_r22sdf_runtime_stage #(
    parameter integer IN_W      = 16,
    parameter integer OUT_W     = IN_W + 1,
    parameter integer MAX_DELAY = 512,
    parameter integer TYPE_II   = 0
) (
    input  wire logic                   clk,
    input  wire logic                   rst_n,
    input  wire logic                   clear,
    input  wire logic [3:0]             delay_log2,
    input  wire logic                   in_token_valid,
    input  wire logic                   in_tag,
    input  wire logic signed [IN_W-1:0] in_re,
    input  wire logic signed [IN_W-1:0] in_im,
    input  wire logic                   in_overflow,
    output logic                        out_token_valid,
    output logic                        out_tag,
    output logic signed [OUT_W-1:0]     out_re,
    output logic signed [OUT_W-1:0]     out_im,
    output logic                        out_overflow,
    output logic [9:0]                  out_local_ordinal
);

    localparam integer ADDR_W = (MAX_DELAY <= 1) ? 1 : $clog2(MAX_DELAY);

    logic signed [OUT_W-1:0] delay_re [0:MAX_DELAY-1];
    logic signed [OUT_W-1:0] delay_im [0:MAX_DELAY-1];
    logic                    delay_tag [0:MAX_DELAY-1];
    logic                    delay_overflow [0:MAX_DELAY-1];

    // Eleven bits cover the largest explicit 4*D period (4*512 = 2048).
    logic [10:0] sample_counter;
    logic [9:0]  fill_count;
    logic        filled;
    logic [10:0] delay_count;
    logic [10:0] delay_minus_one;
    logic [10:0] period_minus_one;
    logic [10:0] local_ordinal;
    logic [ADDR_W-1:0] address;
    logic compute_phase;
    logic rotate_region;

    logic signed [OUT_W-1:0] stored_re;
    logic signed [OUT_W-1:0] stored_im;
    logic                    stored_tag;
    logic                    stored_overflow;
    logic signed [IN_W-1:0]  stored_re_narrow;
    logic signed [IN_W-1:0]  stored_im_narrow;

    logic signed [OUT_W-1:0] bf_direct_re;
    logic signed [OUT_W-1:0] bf_direct_im;
    logic signed [OUT_W-1:0] bf_feedback_re;
    logic signed [OUT_W-1:0] bf_feedback_im;
    logic                    bf_overflow_direct;
    logic                    bf_overflow_feedback;
    logic                    bf_overflow_unused;

    always_comb begin
        delay_count = 11'd1 << delay_log2;
        delay_minus_one = delay_count - 11'd1;
        period_minus_one = (delay_count << 2) - 11'd1;
        address = sample_counter[ADDR_W-1:0] &
            delay_minus_one[ADDR_W-1:0];
        compute_phase = sample_counter[delay_log2];
        rotate_region = sample_counter[delay_log2 + 1'b1];
        local_ordinal = (sample_counter - delay_count) & period_minus_one;
    end

    assign stored_re = delay_re[address];
    assign stored_im = delay_im[address];
    assign stored_tag = delay_tag[address];
    assign stored_overflow = delay_overflow[address];
    assign stored_re_narrow = stored_re[IN_W-1:0];
    assign stored_im_narrow = stored_im[IN_W-1:0];

    generate
        if (TYPE_II == 0) begin : g_type_i
            fft20_bf_type1 #(.IN_W(IN_W), .OUT_W(OUT_W)) u_butterfly (
                .delay_re(stored_re_narrow), .delay_im(stored_im_narrow),
                .input_re(in_re), .input_im(in_im),
                .direct_re(bf_direct_re), .direct_im(bf_direct_im),
                .feedback_re(bf_feedback_re), .feedback_im(bf_feedback_im),
                .overflow_direct(bf_overflow_direct),
                .overflow_feedback(bf_overflow_feedback),
                .overflow(bf_overflow_unused));
        end else begin : g_type_ii
            fft20_bf_type2 #(.IN_W(IN_W), .OUT_W(OUT_W)) u_butterfly (
                .delay_re(stored_re_narrow), .delay_im(stored_im_narrow),
                .input_re(in_re), .input_im(in_im),
                .rotate_minus_j(rotate_region),
                .direct_re(bf_direct_re), .direct_im(bf_direct_im),
                .feedback_re(bf_feedback_re), .feedback_im(bf_feedback_im),
                .overflow_direct(bf_overflow_direct),
                .overflow_feedback(bf_overflow_feedback),
                .overflow(bf_overflow_unused));
        end
    endgenerate

    always_ff @(posedge clk) begin
        if (!rst_n || clear) begin
            sample_counter <= '0;
            fill_count <= '0;
            filled <= 1'b0;
            out_token_valid <= 1'b0;
            out_tag <= 1'b0;
            out_re <= '0;
            out_im <= '0;
            out_overflow <= 1'b0;
            out_local_ordinal <= '0;
        end else begin
            out_token_valid <= 1'b0;
            out_tag <= 1'b0;
            if (in_token_valid) begin
                out_token_valid <= filled;
                out_local_ordinal <= local_ordinal[9:0];
                if (!compute_phase) begin
                    out_re <= stored_re;
                    out_im <= stored_im;
                    out_tag <= stored_tag;
                    out_overflow <= stored_overflow;
                    delay_re[address] <=
                        {{(OUT_W-IN_W){in_re[IN_W-1]}}, in_re};
                    delay_im[address] <=
                        {{(OUT_W-IN_W){in_im[IN_W-1]}}, in_im};
                    delay_tag[address] <= in_tag;
                    delay_overflow[address] <= in_overflow;
                end else begin
                    out_re <= bf_direct_re;
                    out_im <= bf_direct_im;
                    out_tag <= stored_tag & in_tag;
                    out_overflow <= stored_overflow | in_overflow |
                        bf_overflow_direct;
                    delay_re[address] <= bf_feedback_re;
                    delay_im[address] <= bf_feedback_im;
                    delay_tag[address] <= stored_tag & in_tag;
                    delay_overflow[address] <= stored_overflow | in_overflow |
                        bf_overflow_feedback;
                end

                // Runtime periods are explicit: every stage wraps at 4*D.
                if (sample_counter == period_minus_one)
                    sample_counter <= '0;
                else
                    sample_counter <= sample_counter + 11'd1;

                if (!filled) begin
                    if ({1'b0, fill_count} == delay_minus_one) begin
                        filled <= 1'b1;
                    end else begin
                        fill_count <= fill_count + 10'd1;
                    end
                end
            end
        end
    end

endmodule

`default_nettype wire
