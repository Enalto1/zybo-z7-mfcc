`default_nettype none
// Integer PCM16 -> raw MFCC13/F24. A clip-start aborts all pending transactions.
// Input may pause and must obey o_pcm_ready. Final output may stall indefinitely.
// At most three frames are admitted beyond the frontend: spectral, log, DCT.
module mfcc_fixed_top #(
    parameter MEL_INIT_FILE = "mel_sparse_fw16.mem"
) (
    input wire logic clk, input wire logic rst_n, input wire logic i_clip_start,
    input wire logic i_pcm_valid, output logic o_pcm_ready,
    input wire logic signed [15:0] i_pcm, input wire logic i_pcm_last,
    output logic o_valid, input wire logic i_ready,
    output logic signed [39:0] o_mfcc, output logic [31:0] o_frame,
    output logic [3:0] o_index, output logic signed [7:0] o_bfp_s,
    output logic o_last, output logic o_error, output logic o_clip_done,
    output logic o_front_valid, output logic o_front_ready,
    output logic signed [15:0] o_front_q,
    output logic [31:0] o_front_frame, output logic [8:0] o_front_index,
    output logic signed [7:0] o_front_s,
    output logic o_fft_valid, output logic signed [19:0] o_fft_re,
    output logic signed [19:0] o_fft_im, output logic [8:0] o_fft_bin,
    output logic o_fft_last,
    output logic o_mel_valid, output logic o_mel_ready,
    output logic [59:0] o_mel_value, output logic [4:0] o_mel_band,
    output logic [31:0] o_mel_frame, output logic signed [7:0] o_mel_s
);
    typedef enum logic [1:0] {S_ACTIVE, S_DRAIN} state_t;
    state_t c_state, n_state;
    logic pipeline_rst_n, front_last, front_error, front_done;
    logic mel_last, mel_overflow, mel_error, spectral_busy;
    logic frame_error_reg, frame_error_next;
    logic [2:0] inflight_reg, inflight_next;
    logic done_reg, done_next;
    logic frame_admitted, frame_emitted;
    assign pipeline_rst_n = rst_n && !i_clip_start;
    assign frame_admitted = o_front_valid && o_front_ready && (o_front_index == 9'd0);
    assign frame_emitted = o_valid && i_ready && o_last;
    assign o_clip_done = done_reg;
    mfcc_fixed_frontend U_FRONT (
        .clk(clk), .rst_n(rst_n), .i_clip_start(i_clip_start),
        .i_pcm_valid(i_pcm_valid), .o_pcm_ready(o_pcm_ready), .i_pcm(i_pcm), .i_pcm_last(i_pcm_last),
        .o_valid(o_front_valid), .i_ready(o_front_ready), .o_fft(o_front_q),
        .o_frame(o_front_frame), .o_index(o_front_index), .o_bfp_s(o_front_s),
        .o_last(front_last), .o_error(front_error), .o_clip_done(front_done)
    );
    fixed_spectral_top #(.MEL_INIT_FILE(MEL_INIT_FILE)) U_SPECTRAL (
        .clk(clk), .rst_n(pipeline_rst_n), .i_valid(o_front_valid), .o_ready(o_front_ready),
        .i_re(o_front_q), .i_im(16'sd0), .i_last(front_last),
        .i_frame_id(o_front_frame), .i_bfp_s(o_front_s), .o_valid(o_mel_valid), .i_ready(o_mel_ready),
        .o_mel(o_mel_value), .o_band(o_mel_band), .o_frame_id(o_mel_frame), .o_bfp_s(o_mel_s),
        .o_last(mel_last), .o_overflow(mel_overflow), .o_protocol_error(mel_error), .o_busy(spectral_busy),
        .o_fft_valid(o_fft_valid), .o_fft_re(o_fft_re), .o_fft_im(o_fft_im), .o_fft_bin(o_fft_bin), .o_fft_last(o_fft_last)
    );
    mfcc_fixed_log_dct U_BACK (
        .clk(clk), .rst_n(pipeline_rst_n), .i_valid(o_mel_valid), .o_ready(o_mel_ready),
        .i_mel(o_mel_value), .i_bfp_s(o_mel_s), .i_frame(o_mel_frame), .i_index(o_mel_band),
        .i_last(mel_last), .i_error(mel_error || mel_overflow || frame_error_reg),
        .o_valid(o_valid), .i_ready(i_ready), .o_mfcc(o_mfcc), .o_frame(o_frame),
        .o_index(o_index), .o_bfp_s(o_bfp_s), .o_last(o_last), .o_error(o_error)
    );
    always_ff @(posedge clk) begin
        if (!rst_n || i_clip_start) begin
            frame_error_reg <= 1'b0;
            inflight_reg <= 3'd0;
            c_state <= S_ACTIVE;
            done_reg <= 1'b0;
        end else begin
            frame_error_reg <= frame_error_next;
            inflight_reg <= inflight_next;
            c_state <= n_state;
            done_reg <= done_next;
        end
    end
    always_comb begin
        frame_error_next = frame_error_reg;
        inflight_next = inflight_reg;
        n_state = c_state;
        done_next = 1'b0;
        if (frame_admitted) frame_error_next = front_error;
        case ({frame_admitted, frame_emitted})
            2'b10: inflight_next = inflight_reg + 3'd1;
            2'b01: inflight_next = inflight_reg - 3'd1;
            default: inflight_next = inflight_reg;
        endcase
        // Explicit registered completion states, with invalid-state recovery.
        // Drain is observed only after frontend EOF; counters include every
        // downstream accepted frame until its final MFCC is accepted.
        case (c_state)
            S_ACTIVE: if (front_done) n_state = S_DRAIN;
            S_DRAIN: if (inflight_reg == 3'd0) begin
                done_next = 1'b1;
                n_state = S_ACTIVE;
            end
            default: n_state = S_ACTIVE;
        endcase
    end
endmodule
`default_nettype wire
