// Single-clock PCM16 -> raw C0..C12. No microphone, DMA, PS or clock crossing.
module fp32_mfcc (
    input logic clk, input logic rst_n,
    input logic clip_start_valid, output logic clip_start_ready,
    input logic s_valid, output logic s_ready, input logic [15:0] s_pcm,
    input logic clip_end_valid, output logic clip_end_ready,
    output logic m_valid, input logic m_ready, output logic [31:0] m_data,
    output logic [3:0] m_coeff_index, output logic [31:0] m_frame_id,
    output logic [31:0] m_start_sample, output logic m_last,
    output logic clip_done_valid, input logic clip_done_ready,
    output logic [11:0] o_error, output logic o_busy
);
    typedef enum logic [1:0] {S_IDLE, S_ACTIVE, S_DRAIN, S_DONE} state_t;
    state_t c_state, n_state;
    logic frame_drained_reg, frame_drained_next, error_reg, error_next;
    logic pre_s_valid, pre_s_ready, pre_valid, pre_ready, pre_busy, pre_error;
    logic [31:0] pre_data;
    logic input_dbg_valid;
    logic [31:0] input_dbg_data;
    logic fr_start_valid, fr_start_ready, fr_end_valid, fr_end_ready;
    logic fr_done_valid, fr_done_ready, fr_error, fr_valid, fr_ready, fr_last;
    logic [31:0] fr_data, fr_frame, fr_start;
    logic [8:0] fr_index;
    logic win_valid, win_ready, win_busy, win_error, win_last;
    logic [31:0] win_data, win_frame, win_start;
    logic [8:0] win_index;
    logic backend_busy, backend_done;
    logic [7:0] backend_error;
    // Debug taps are sampled by verification only and disappear from production top I/O.
    logic dbg_valid;
    logic [3:0] dbg_stage;
    logic [15:0] dbg_index;
    logic [31:0] dbg_data, dbg_aux;
    assign o_error = {error_reg, win_error, pre_error, fr_error, backend_error};
    assign o_busy = (c_state != S_IDLE);

    fp32_preemphasis U_PREEMPHASIS (
        .clk(clk), .rst_n(rst_n), .clip_clear(clip_start_valid && clip_start_ready),
        .s_valid(pre_s_valid), .s_ready(pre_s_ready), .s_pcm(s_pcm),
        .m_valid(pre_valid), .m_ready(pre_ready), .m_data(pre_data),
        .o_busy(pre_busy), .o_error(pre_error),
        .dbg_input_valid(input_dbg_valid), .dbg_input_data(input_dbg_data)
    );
    fp32_frame_buffer U_FRAME_BUFFER (
        .clk(clk), .rst_n(rst_n), .clip_start_valid(fr_start_valid), .clip_start_ready(fr_start_ready),
        .s_valid(pre_valid), .s_ready(pre_ready), .s_data(pre_data),
        .clip_end_valid(fr_end_valid), .clip_end_ready(fr_end_ready),
        .m_valid(fr_valid), .m_ready(fr_ready), .m_data(fr_data),
        .m_sample_index(fr_index), .m_frame_id(fr_frame), .m_start_sample(fr_start), .m_last(fr_last),
        .clip_done_valid(fr_done_valid), .clip_done_ready(fr_done_ready), .error(fr_error)
    );
    fp32_window U_WINDOW (
        .clk(clk), .rst_n(rst_n), .s_valid(fr_valid), .s_ready(fr_ready), .s_data(fr_data),
        .s_index(fr_index), .s_frame_id(fr_frame), .s_start_sample(fr_start), .s_last(fr_last),
        .m_valid(win_valid), .m_ready(win_ready), .m_data(win_data), .m_index(win_index),
        .m_frame_id(win_frame), .m_start_sample(win_start), .m_last(win_last),
        .o_busy(win_busy), .o_error(win_error)
    );
    fp32_backend U_BACKEND (
        .clk(clk), .rst_n(rst_n), .s_valid(win_valid), .s_ready(win_ready), .s_data(win_data),
        .s_index(win_index), .s_frame_id(win_frame), .s_start_sample(win_start), .s_last(win_last),
        .m_valid(m_valid), .m_ready(m_ready), .m_data(m_data), .m_coeff_index(m_coeff_index),
        .m_frame_id(m_frame_id), .m_start_sample(m_start_sample), .m_last(m_last),
        .o_busy(backend_busy), .o_error(backend_error), .o_frame_done(backend_done),
        .dbg_valid(dbg_valid), .dbg_stage(dbg_stage), .dbg_index(dbg_index), .dbg_data(dbg_data), .dbg_aux(dbg_aux)
    );
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE;
            frame_drained_reg <= 1'b0;
            error_reg <= 1'b0;
        end else begin
            c_state <= n_state;
            frame_drained_reg <= frame_drained_next;
            error_reg <= error_next;
        end
    end
    always_comb begin
        n_state = c_state;
        frame_drained_next = frame_drained_reg;
        error_next = error_reg;
        clip_start_ready = 1'b0;
        clip_end_ready = 1'b0;
        clip_done_valid = 1'b0;
        s_ready = 1'b0;
        pre_s_valid = 1'b0;
        fr_start_valid = 1'b0;
        fr_end_valid = 1'b0;
        fr_done_ready = 1'b0;
        case (c_state)
            S_IDLE: begin
                clip_start_ready = fr_start_ready;
                fr_start_valid = clip_start_valid;
                if (clip_start_valid && clip_start_ready) begin
                    frame_drained_next = 1'b0;
                    n_state = S_ACTIVE;
                end
            end
            S_ACTIVE: begin
                // EOF may arrive while conversion/filtering is in flight. Drain it first.
                pre_s_valid = s_valid && !clip_end_valid;
                s_ready = pre_s_ready && !clip_end_valid;
                fr_end_valid = clip_end_valid && !pre_busy;
                clip_end_ready = fr_end_ready && !pre_busy;
                if (clip_end_valid && clip_end_ready) n_state = S_DRAIN;
            end
            S_DRAIN: begin
                fr_done_ready = 1'b1;
                if (fr_done_valid) frame_drained_next = 1'b1;
                // Framer done only means all samples entered windowing; drain downstream too.
                if (frame_drained_reg && !win_busy && !backend_busy) n_state = S_DONE;
            end
            S_DONE: begin
                clip_done_valid = 1'b1;
                if (clip_done_ready) n_state = S_IDLE;
            end
            default: begin error_next = 1'b1; n_state = S_IDLE; end
        endcase
    end
endmodule
