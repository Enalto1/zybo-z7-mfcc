`timescale 1ns/1ps
`default_nettype none
// One reserved in-flight frame. The FFT output has no ready port and is never
// stalled. Admission waits until the tail owns storage for every FFT beat.
module fixed_spectral_top #(
    parameter MEL_INIT_FILE = "mel_sparse_fw16.mem"
) (
    input wire logic clk,
    input wire logic rst_n,
    input wire logic i_valid,
    output logic o_ready,
    input wire logic signed [15:0] i_re,
    input wire logic signed [15:0] i_im,
    input wire logic i_last,
    input wire logic [31:0] i_frame_id,
    input wire logic signed [7:0] i_bfp_s,
    output logic o_valid,
    input wire logic i_ready,
    output logic [59:0] o_mel,
    output logic [4:0] o_band,
    output logic [31:0] o_frame_id,
    output logic signed [7:0] o_bfp_s,
    output logic o_last,
    output logic o_overflow,
    output logic o_protocol_error,
    output logic o_busy,
    output logic o_fft_valid,
    output logic signed [19:0] o_fft_re,
    output logic signed [19:0] o_fft_im,
    output logic [8:0] o_fft_bin,
    output logic o_fft_last
);
    typedef enum logic [2:0] { S_BOOT, S_CONFIG, S_IDLE, S_FEED, S_WAIT } state_t;
    state_t c_state, n_state;
    logic [8:0] count_reg, count_next;
    logic [31:0] frame_reg, frame_next;
    logic signed [7:0] bfp_reg, bfp_next;
    logic frame_error_reg, frame_error_next;
    logic cfg_ready, cfg_accept, cfg_reject, cfg_error;
    logic [3:0] active_n;
    logic fft_in_ready, fft_valid, fft_last, fft_ov;
    logic signed [19:0] fft_re, fft_im;
    logic [9:0] fft_index;
    logic fft_busy, fft_done, fft_overflow;
    logic input_error, reorder_error, collision_error, consistency_error;
    logic tail_ready, tail_error;
    logic admission, take, reserve;
    logic [8:0] accepted_index;
    logic signed [19:0] promoted_re, promoted_im;

    assign admission = ((c_state == S_IDLE) && tail_ready) || (c_state == S_FEED);
    assign o_ready = admission && fft_in_ready && rst_n;
    assign take = i_valid && o_ready;
    assign reserve = take && (c_state == S_IDLE);
    assign accepted_index = (c_state == S_IDLE) ? 9'd0 : count_reg;
    // Concatenation is exact sign-preserving Q16/F15 -> Q20/F19 promotion.
    assign promoted_re = {i_re, 4'b0000};
    assign promoted_im = {i_im, 4'b0000};
    assign o_busy = (c_state != S_IDLE) || fft_busy;
    assign o_protocol_error = tail_error || frame_error_reg || cfg_error;
    assign o_fft_valid = fft_valid;
    assign o_fft_re = fft_re;
    assign o_fft_im = fft_im;
    assign o_fft_bin = fft_index[8:0];
    assign o_fft_last = fft_last;

    fft20_stream_top U_FFT (
        .clk(clk), .rst_n(rst_n), .cfg_log2_n(4'd9),
        .cfg_apply(c_state == S_CONFIG), .cfg_ready(cfg_ready),
        .cfg_accept(cfg_accept), .cfg_reject(cfg_reject), .cfg_error(cfg_error),
        .active_log2_n(active_n), .in_valid(i_valid && admission && rst_n),
        .in_ready(fft_in_ready), .in_re(promoted_re), .in_im(promoted_im),
        .in_last(accepted_index == 9'd511), .out_valid(fft_valid),
        .out_re(fft_re), .out_im(fft_im), .out_index(fft_index),
        .out_last(fft_last), .out_overflow(fft_ov), .busy(fft_busy),
        .done(fft_done), .overflow(fft_overflow), .input_frame_error(input_error),
        .reorder_frame_error(reorder_error), .bank_collision_error(collision_error),
        .config_consistency_error(consistency_error)
    );
    fixed_spectral_tail #(.MEL_INIT_FILE(MEL_INIT_FILE)) U_TAIL (
        .clk(clk), .rst_n(rst_n), .i_frame_start(reserve), .o_frame_ready(tail_ready),
        .i_frame_id(i_frame_id), .i_bfp_s(i_bfp_s), .i_fft_valid(fft_valid),
        .i_fft_re(fft_re), .i_fft_im(fft_im), .i_fft_bin(fft_index[8:0]),
        .i_fft_last(fft_last), .i_fft_overflow(fft_ov), .o_valid(o_valid),
        .i_ready(i_ready), .o_mel(o_mel), .o_band(o_band),
        .o_frame_id(o_frame_id), .o_bfp_s(o_bfp_s), .o_last(o_last),
        .o_overflow(o_overflow), .o_protocol_error(tail_error),
        .o_power_valid(), .o_power(), .o_power_bin() // Unit TB checks these separately.
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_BOOT;
            count_reg <= 9'd0;
            frame_reg <= 32'd0;
            bfp_reg <= 8'sd0;
            frame_error_reg <= 1'b0;
        end else begin
            c_state <= n_state;
            count_reg <= count_next;
            frame_reg <= frame_next;
            bfp_reg <= bfp_next;
            frame_error_reg <= frame_error_next;
        end
    end

    always_comb begin
        n_state = c_state;
        count_next = count_reg;
        frame_next = frame_reg;
        bfp_next = bfp_reg;
        frame_error_next = frame_error_reg;
        if (input_error || reorder_error || collision_error || consistency_error)
            frame_error_next = 1'b1;
        case (c_state)
            S_BOOT: begin
                if (cfg_ready) n_state = S_CONFIG;
            end
            S_CONFIG: begin
                // cfg_accept is registered; configuration is presented until accepted.
                if (cfg_accept) n_state = S_IDLE;
                if (cfg_reject) n_state = S_BOOT;
            end
            S_IDLE: begin
                if (take) begin
                    frame_next = i_frame_id;
                    bfp_next = i_bfp_s;
                    count_next = 9'd1;
                    frame_error_next = i_last || (i_bfp_s < -8'sd2) || (i_bfp_s > 8'sd24);
                    n_state = S_FEED;
                end
            end
            S_FEED: begin
                if (take) begin
                    if ((i_last != (count_reg == 9'd511)) ||
                        (i_frame_id != frame_reg) || (i_bfp_s != bfp_reg))
                        frame_error_next = 1'b1;
                    if (count_reg == 9'd511) n_state = S_WAIT;
                    else count_next = count_reg + 9'd1;
                end
            end
            S_WAIT: begin
                if (o_valid && i_ready && o_last) n_state = S_IDLE;
            end
            default: begin
                n_state = S_BOOT;
                count_next = 9'd0;
                frame_error_next = 1'b1;
            end
        endcase
    end
endmodule
`default_nettype wire
