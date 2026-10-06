`default_nettype none
// Pulse/last transport to the native FP32 start/end/done stream protocol.
// Transport reset is registered: at least16 low clocks, then a conservative
// four-clock high guard before another reset or a native handshake. Commands
// during that guard are queued. No arithmetic or coefficient conversion.
module fp32_core_adapter (
    input wire logic clk, input wire logic rst_n,
    input wire logic i_clip_start, input wire logic i_abort,
    input wire logic i_pcmvalid, output logic o_pcmready,
    input wire logic signed [15:0] i_pcm, input wire logic i_pcm_last,
    output logic o_outputvalid, input wire logic i_outputready,
    output logic [63:0] o_data64, output logic [31:0] o_frame32,
    output logic [3:0] o_index4, output logic signed [7:0] o_bfp8,
    output logic o_last, output logic o_error, output logic o_metadata_error,
    output logic [11:0] o_core_error_detail, output logic o_clipdone,
    output logic o_core_rst_n,
    output logic o_start_valid, input wire logic i_start_ready,
    output logic o_sample_valid, input wire logic i_sample_ready,
    output logic [15:0] o_sample_pcm,
    output logic o_end_valid, input wire logic i_end_ready,
    input wire logic i_result_valid, output logic o_result_ready,
    input wire logic [31:0] i_result_data, input wire logic [3:0] i_result_index,
    input wire logic [31:0] i_result_frame, input wire logic [31:0] i_result_start,
    input wire logic i_result_last,
    input wire logic i_done_valid, output logic o_done_ready,
    input wire logic [11:0] i_core_error
);
    typedef enum logic [2:0] {S_RESET, S_RELEASE, S_IDLE, S_START, S_ACTIVE, S_END, S_DRAIN} state_t;
    localparam logic [4:0] RESET_CLOCKS = 5'd16;
    localparam logic [2:0] RELEASE_CLOCKS = 3'd4;
    state_t c_state, n_state;
    logic [4:0] reset_count_reg, reset_count_next;
    logic [2:0] release_count_reg, release_count_next;
    logic core_reset_n_reg, core_reset_n_next, reset_pending_reg, reset_pending_next;
    logic start_pending_reg, start_pending_next, empty_reg, empty_next;
    logic done_reg, done_next, metadata_error_reg, metadata_error_next;
    logic [11:0] error_reg, error_next;
    logic native_enabled, output_enabled, bad_start_sample;
    logic [63:0] expected_start_sample;

    // External bus reset still takes precedence. Transport command edges do
    // not drive the reset net combinationally, avoiding shortened/delta pulses.
    assign o_core_rst_n = rst_n && core_reset_n_reg;
    assign o_data64 = {32'd0, i_result_data};
    assign o_frame32 = i_result_frame;
    assign o_index4 = i_result_index;
    assign o_bfp8 = 8'sd0;
    assign o_last = i_result_last;
    assign o_sample_pcm = i_pcm;
    assign expected_start_sample = ({32'd0, i_result_frame} << 7) + ({32'd0, i_result_frame} << 5);
    assign bad_start_sample = ({32'd0, i_result_start} != expected_start_sample);
    assign native_enabled = o_core_rst_n && !i_abort && !i_clip_start && !reset_pending_reg &&
                            ((c_state == S_START) || (c_state == S_ACTIVE) || (c_state == S_END) || (c_state == S_DRAIN));
    assign output_enabled = native_enabled && (c_state != S_START);
    assign o_outputvalid = output_enabled && i_result_valid;
    assign o_result_ready = output_enabled && i_outputready;
    // Native backend[7:0] is an error CODE, so OR accumulation could invent a
    // code. Freeze the first nonzero native12-bit vector exactly.
    assign o_core_error_detail = (error_reg != 12'd0) ? error_reg : (native_enabled ? i_core_error : 12'd0);
    assign o_error = (|o_core_error_detail) || o_metadata_error;
    assign o_metadata_error = metadata_error_reg || (o_outputvalid && bad_start_sample);
    assign o_clipdone = done_reg;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_RESET;
            reset_count_reg <= RESET_CLOCKS;
            release_count_reg <= RELEASE_CLOCKS;
            core_reset_n_reg <= 1'b0;
            reset_pending_reg <= 1'b0;
            start_pending_reg <= 1'b0;
            empty_reg <= 1'b0;
            done_reg <= 1'b0;
            metadata_error_reg <= 1'b0;
            error_reg <= 12'd0;
        end else begin
            c_state <= n_state;
            reset_count_reg <= reset_count_next;
            release_count_reg <= release_count_next;
            core_reset_n_reg <= core_reset_n_next;
            reset_pending_reg <= reset_pending_next;
            start_pending_reg <= start_pending_next;
            empty_reg <= empty_next;
            done_reg <= done_next;
            metadata_error_reg <= metadata_error_next;
            error_reg <= error_next;
        end
    end

    always_comb begin
        n_state = c_state;
        reset_count_next = reset_count_reg;
        release_count_next = release_count_reg;
        core_reset_n_next = core_reset_n_reg;
        reset_pending_next = reset_pending_reg;
        start_pending_next = start_pending_reg;
        empty_next = empty_reg;
        done_next = 1'b0;
        metadata_error_next = metadata_error_reg;
        error_next = error_reg;
        o_start_valid = 1'b0;
        o_sample_valid = 1'b0;
        o_pcmready = 1'b0;
        o_end_valid = 1'b0;
        o_done_ready = 1'b0;
        if (native_enabled && (error_reg == 12'd0)) error_next = i_core_error;
        if (o_outputvalid && i_outputready && bad_start_sample) metadata_error_next = 1'b1;
        case (c_state)
            S_RESET: begin
                if (reset_count_reg > 5'd1) reset_count_next = reset_count_reg - 5'd1;
                else begin
                    reset_count_next = 5'd0;
                    core_reset_n_next = 1'b1;
                    release_count_next = RELEASE_CLOCKS;
                    n_state = S_RELEASE;
                end
            end
            S_RELEASE: begin
                if (release_count_reg > 3'd1) release_count_next = release_count_reg - 3'd1;
                else begin
                    release_count_next = 3'd0;
                    if (reset_pending_reg) begin
                        core_reset_n_next = 1'b0;
                        reset_count_next = RESET_CLOCKS;
                        reset_pending_next = 1'b0;
                        n_state = S_RESET;
                    end else if (start_pending_reg) n_state = S_START;
                    else n_state = S_IDLE;
                end
            end
            S_IDLE: begin end
            S_START: begin
                o_start_valid = native_enabled;
                if (o_start_valid && i_start_ready) begin
                    start_pending_next = 1'b0;
                    if (empty_reg) n_state = S_END;
                    else n_state = S_ACTIVE;
                end
            end
            S_ACTIVE: begin
                o_sample_valid = native_enabled && i_pcmvalid;
                o_pcmready = native_enabled && i_sample_ready;
                if (o_sample_valid && i_sample_ready && i_pcm_last) n_state = S_END;
            end
            S_END: begin
                // EOF remains asserted until the native pre-emphasis pipeline
                // drains and accepts it. It never masks the final PCM transfer.
                o_end_valid = native_enabled;
                if (o_end_valid && i_end_ready) n_state = S_DRAIN;
            end
            S_DRAIN: begin
                o_done_ready = native_enabled;
                if (i_done_valid && o_done_ready) begin
                    done_next = 1'b1;
                    n_state = S_IDLE;
                end
            end
            default: begin
                n_state = S_RESET;
                reset_count_next = RESET_CLOCKS;
                core_reset_n_next = 1'b0;
                reset_pending_next = 1'b0;
                start_pending_next = 1'b0;
                metadata_error_next = 1'b1;
            end
        endcase
        // Transport control has final priority. ABORT cancels even a coincident
        // START and any pending completion. New START begins a fresh clip.
        if (i_clip_start) begin
            if ((c_state == S_RELEASE) && (release_count_reg > 3'd1)) reset_pending_next = 1'b1;
            else begin
                n_state = S_RESET;
                reset_count_next = RESET_CLOCKS;
                core_reset_n_next = 1'b0;
                reset_pending_next = 1'b0;
            end
            start_pending_next = 1'b1;
            empty_next = i_pcm_last;
            done_next = 1'b0;
            error_next = 12'd0;
            metadata_error_next = 1'b0;
        end
        if (i_abort) begin
            if ((c_state == S_RELEASE) && (release_count_reg > 3'd1)) reset_pending_next = 1'b1;
            else begin
                n_state = S_RESET;
                reset_count_next = RESET_CLOCKS;
                core_reset_n_next = 1'b0;
                reset_pending_next = 1'b0;
            end
            start_pending_next = 1'b0;
            empty_next = 1'b0;
            done_next = 1'b0;
            error_next = 12'd0;
            metadata_error_next = 1'b0;
        end
    end
endmodule
`default_nettype wire
