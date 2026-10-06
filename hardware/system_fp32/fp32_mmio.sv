`default_nettype none
// ABI v1.1 FP32 fork: independent AXI address/data holding registers and one record per
// core stream direction. All arithmetic payloads pass through unchanged.
module fp32_mmio #(
    parameter logic [31:0] CORE_ID = 32'd2,
    parameter logic [31:0] FORMAT = 32'h00030020,
    parameter logic [31:0] CONTRACT_TAG = 32'hc556a8e8
) (
    input wire logic s_axi_aclk,
    input wire logic s_axi_aresetn,
    input wire logic [15:0] s_axi_awaddr,
    input wire logic [2:0] s_axi_awprot,
    input wire logic s_axi_awvalid,
    output logic s_axi_awready,
    input wire logic [31:0] s_axi_wdata,
    input wire logic [3:0] s_axi_wstrb,
    input wire logic s_axi_wvalid,
    output logic s_axi_wready,
    output logic [1:0] s_axi_bresp,
    output logic s_axi_bvalid,
    input wire logic s_axi_bready,
    input wire logic [15:0] s_axi_araddr,
    input wire logic [2:0] s_axi_arprot,
    input wire logic s_axi_arvalid,
    output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,
    output logic [1:0] s_axi_rresp,
    output logic s_axi_rvalid,
    input wire logic s_axi_rready,
    output logic o_clip_start,
    output logic o_abort,
    output logic o_pcmvalid,
    input wire logic i_pcmready,
    output logic signed [15:0] o_pcm,
    output logic o_pcm_last,
    input wire logic i_outputvalid,
    output logic o_outputready,
    input wire logic [63:0] i_data64,
    input wire logic [31:0] i_frame32,
    input wire logic [3:0] i_index4,
    input wire logic signed [7:0] i_bfp8,
    input wire logic i_last,
    input wire logic i_error,
    input wire logic i_metadata_error,
    input wire logic [11:0] i_core_error_detail,
    input wire logic i_clipdone
);
    typedef enum logic [1:0] {S_IDLE = 2'd0, S_BUSY = 2'd1} state_t;
    localparam logic [1:0] RESP_OKAY = 2'b00;
    localparam logic [1:0] RESP_SLVERR = 2'b10;
    localparam logic [31:0] MAX_SAMPLES = 32'd262144;

    state_t c_state, n_state;
    logic aw_pending_reg, aw_pending_next, w_pending_reg, w_pending_next;
    logic [15:0] aw_addr_reg, aw_addr_next;
    logic [31:0] w_data_reg, w_data_next;
    logic [3:0] w_strb_reg, w_strb_next;
    logic b_valid_reg, b_valid_next, r_valid_reg, r_valid_next;
    logic [1:0] b_resp_reg, b_resp_next, r_resp_reg, r_resp_next;
    logic [31:0] r_data_reg, r_data_next;
    logic [31:0] sample_count_reg, sample_count_next;
    logic done_reg, done_next;
    logic [5:0] error_reg, error_next;
    logic [11:0] core_error_reg, core_error_next;
    logic clip_start_reg, clip_start_next, abort_reg, abort_next;
    logic pcm_valid_reg, pcm_valid_next, pcm_last_reg, pcm_last_next;
    logic signed [15:0] pcm_reg, pcm_next;
    logic output_valid_reg, output_valid_next;
    logic [63:0] output_data_reg, output_data_next;
    logic [31:0] output_frame_reg, output_frame_next, output_meta_reg, output_meta_next;
    logic [31:0] written_reg, written_next, consumed_reg, consumed_next;
    logic [31:0] captured_reg, captured_next, popped_reg, popped_next;
    logic [63:0] cycles_reg, cycles_next;
    logic [9:0] frame_remaining_reg, frame_remaining_next;
    logic [31:0] expected_outputs_reg, expected_outputs_next;
    logic [31:0] expected_frame_reg, expected_frame_next;
    logic [3:0] expected_index_reg, expected_index_next;
    logic signed [7:0] frame_bfp_reg, frame_bfp_next;
    logic pcm_can_write, core_enabled;

    // PROT is intentionally ignored: this ABI adds no security/access policy.
    // Separate AW/W acceptance permits either channel to arrive first.
    assign s_axi_awready = s_axi_aresetn && !aw_pending_reg && !b_valid_reg;
    assign s_axi_wready = s_axi_aresetn && !w_pending_reg && !b_valid_reg;
    assign s_axi_bvalid = b_valid_reg;
    assign s_axi_bresp = b_resp_reg;
    assign s_axi_arready = s_axi_aresetn && !r_valid_reg;
    assign s_axi_rvalid = r_valid_reg;
    assign s_axi_rdata = r_data_reg;
    assign s_axi_rresp = r_resp_reg;
    assign o_clip_start = clip_start_reg;
    assign o_abort = abort_reg;
    assign core_enabled = s_axi_aresetn && (c_state == S_BUSY) && !clip_start_reg && !abort_reg;
    assign pcm_can_write = core_enabled && !pcm_valid_reg && (written_reg < sample_count_reg);
    assign o_pcmvalid = core_enabled && pcm_valid_reg;
    assign o_pcm = pcm_reg;
    // An empty clip has no PCM transaction: last accompanies its start pulse.
    assign o_pcm_last = pcm_last_reg || (clip_start_reg && (sample_count_reg == 32'd0));
    assign o_outputready = core_enabled && !output_valid_reg;

    always_ff @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            c_state <= S_IDLE;
            aw_pending_reg <= 1'b0;
            w_pending_reg <= 1'b0;
            aw_addr_reg <= 16'd0;
            w_data_reg <= 32'd0;
            w_strb_reg <= 4'd0;
            b_valid_reg <= 1'b0;
            b_resp_reg <= RESP_OKAY;
            r_valid_reg <= 1'b0;
            r_resp_reg <= RESP_OKAY;
            r_data_reg <= 32'd0;
            sample_count_reg <= 32'd0;
            done_reg <= 1'b0;
            error_reg <= 6'd0;
            core_error_reg <= 12'd0;
            clip_start_reg <= 1'b0;
            abort_reg <= 1'b0;
            pcm_valid_reg <= 1'b0;
            pcm_reg <= 16'sd0;
            pcm_last_reg <= 1'b0;
            output_valid_reg <= 1'b0;
            output_data_reg <= 64'd0;
            output_frame_reg <= 32'd0;
            output_meta_reg <= 32'd0;
            written_reg <= 32'd0;
            consumed_reg <= 32'd0;
            captured_reg <= 32'd0;
            popped_reg <= 32'd0;
            cycles_reg <= 64'd0;
            frame_remaining_reg <= 10'd512;
            expected_outputs_reg <= 32'd0;
            expected_frame_reg <= 32'd0;
            expected_index_reg <= 4'd0;
            frame_bfp_reg <= 8'sd0;
        end else begin
            c_state <= n_state;
            aw_pending_reg <= aw_pending_next;
            w_pending_reg <= w_pending_next;
            aw_addr_reg <= aw_addr_next;
            w_data_reg <= w_data_next;
            w_strb_reg <= w_strb_next;
            b_valid_reg <= b_valid_next;
            b_resp_reg <= b_resp_next;
            r_valid_reg <= r_valid_next;
            r_resp_reg <= r_resp_next;
            r_data_reg <= r_data_next;
            sample_count_reg <= sample_count_next;
            done_reg <= done_next;
            error_reg <= error_next;
            core_error_reg <= core_error_next;
            clip_start_reg <= clip_start_next;
            abort_reg <= abort_next;
            pcm_valid_reg <= pcm_valid_next;
            pcm_reg <= pcm_next;
            pcm_last_reg <= pcm_last_next;
            output_valid_reg <= output_valid_next;
            output_data_reg <= output_data_next;
            output_frame_reg <= output_frame_next;
            output_meta_reg <= output_meta_next;
            written_reg <= written_next;
            consumed_reg <= consumed_next;
            captured_reg <= captured_next;
            popped_reg <= popped_next;
            cycles_reg <= cycles_next;
            frame_remaining_reg <= frame_remaining_next;
            expected_outputs_reg <= expected_outputs_next;
            expected_frame_reg <= expected_frame_next;
            expected_index_reg <= expected_index_next;
            frame_bfp_reg <= frame_bfp_next;
        end
    end

    always_comb begin
        n_state = c_state;
        aw_pending_next = aw_pending_reg;
        w_pending_next = w_pending_reg;
        aw_addr_next = aw_addr_reg;
        w_data_next = w_data_reg;
        w_strb_next = w_strb_reg;
        b_valid_next = b_valid_reg;
        b_resp_next = b_resp_reg;
        r_valid_next = r_valid_reg;
        r_resp_next = r_resp_reg;
        r_data_next = r_data_reg;
        sample_count_next = sample_count_reg;
        done_next = done_reg;
        error_next = error_reg;
        core_error_next = core_error_reg;
        clip_start_next = 1'b0;
        abort_next = 1'b0;
        pcm_valid_next = pcm_valid_reg;
        pcm_next = pcm_reg;
        pcm_last_next = pcm_last_reg;
        output_valid_next = output_valid_reg;
        output_data_next = output_data_reg;
        output_frame_next = output_frame_reg;
        output_meta_next = output_meta_reg;
        written_next = written_reg;
        consumed_next = consumed_reg;
        captured_next = captured_reg;
        popped_next = popped_reg;
        cycles_next = cycles_reg;
        frame_remaining_next = frame_remaining_reg;
        expected_outputs_next = expected_outputs_reg;
        expected_frame_next = expected_frame_reg;
        expected_index_next = expected_index_reg;
        frame_bfp_next = frame_bfp_reg;

        if (s_axi_awvalid && s_axi_awready) begin
            aw_pending_next = 1'b1;
            aw_addr_next = s_axi_awaddr;
        end
        if (s_axi_wvalid && s_axi_wready) begin
            w_pending_next = 1'b1;
            w_data_next = s_axi_wdata;
            w_strb_next = s_axi_wstrb;
        end
        if (b_valid_reg && s_axi_bready) b_valid_next = 1'b0;
        if (r_valid_reg && s_axi_rready) r_valid_next = 1'b0;

        case (c_state)
            S_IDLE: begin
                // Pending diagnostic output remains readable after completion.
            end
            S_BUSY: begin
                cycles_next = cycles_reg + 64'd1;
                if (core_enabled) begin
                    if (o_pcmvalid && i_pcmready) begin
                        pcm_valid_next = 1'b0;
                        pcm_last_next = 1'b0;
                        consumed_next = consumed_reg + 32'd1;
                        // First full frame after512 PCM, then another per160.
                        // This is exact for overlapping frames without division.
                        if (frame_remaining_reg == 10'd1) begin
                            frame_remaining_next = 10'd160;
                            expected_outputs_next = expected_outputs_reg + 32'd13;
                        end else begin
                            frame_remaining_next = frame_remaining_reg - 10'd1;
                        end
                    end
                    if (i_error) error_next[3] = 1'b1;
                    if (i_metadata_error) error_next[4] = 1'b1;
                    if (core_error_reg == 12'd0) core_error_next = i_core_error_detail;
                    if (i_outputvalid && o_outputready) begin
                        output_valid_next = 1'b1;
                        output_data_next = i_data64;
                        output_frame_next = i_frame32;
                        output_meta_next = {14'd0, (i_error || i_metadata_error), i_last, i_bfp8, 4'd0, i_index4};
                        captured_next = captured_reg + 32'd1;
                        if ((i_frame32 != expected_frame_reg) || (i_index4 != expected_index_reg) ||
                            (i_last != (expected_index_reg == 4'd12))) error_next[4] = 1'b1;
                        if (expected_index_reg == 4'd0) frame_bfp_next = i_bfp8;
                        else if (i_bfp8 != frame_bfp_reg) error_next[4] = 1'b1;
                        if (expected_index_reg == 4'd12) begin
                            expected_index_next = 4'd0;
                            expected_frame_next = expected_frame_reg + 32'd1;
                        end else begin
                            expected_index_next = expected_index_reg + 4'd1;
                        end
                    end
                end
                // Completion can be immediate for an empty clip, including
                // its clip-start cycle. An abort pulse cancels completion.
                if (i_clipdone && !abort_reg) begin
                    n_state = S_IDLE;
                    done_next = 1'b1;
                    // Include transfers accepted on this same clock edge.
                    if ((written_next != sample_count_reg) || (consumed_next != sample_count_reg) ||
                        (captured_next != expected_outputs_next) || pcm_valid_next)
                        error_next[5] = 1'b1;
                end
            end
            default: begin
                // Invalid state aborts arithmetic and flushes slots; AXI state
                // remains live so software can diagnose bit5 and recover.
                n_state = S_IDLE;
                abort_next = 1'b1;
                done_next = 1'b0;
                pcm_valid_next = 1'b0;
                pcm_last_next = 1'b0;
                output_valid_next = 1'b0;
                error_next[5] = 1'b1;
            end
        endcase

        // Writes commit only after both independent channels were registered.
        // START/CLEAR/ABORT clear previous errors after ordinary core activity;
        // a new bad read below has final priority and is never lost.
        if (aw_pending_reg && w_pending_reg && !b_valid_reg) begin
            aw_pending_next = 1'b0;
            w_pending_next = 1'b0;
            b_valid_next = 1'b1;
            b_resp_next = RESP_OKAY;
            if ((aw_addr_reg[1:0] != 2'b00) || (w_strb_reg != 4'hf)) begin
                b_resp_next = RESP_SLVERR;
                error_next[0] = 1'b1;
            end else begin
                case (aw_addr_reg)
                    16'h0008: begin
                        case (w_data_reg)
                            32'd0: begin end
                            32'd1: begin
                                if ((c_state != S_IDLE) || output_valid_reg) begin
                                    b_resp_next = RESP_SLVERR;
                                    error_next[1] = 1'b1;
                                end else begin
                                    n_state = S_BUSY;
                                    done_next = 1'b0;
                                    error_next = 6'd0;
                                    core_error_next = 12'd0;
                                    clip_start_next = 1'b1;
                                    abort_next = 1'b0;
                                    pcm_valid_next = 1'b0;
                                    pcm_next = 16'sd0;
                                    pcm_last_next = 1'b0;
                                    output_valid_next = 1'b0;
                                    output_data_next = 64'd0;
                                    output_frame_next = 32'd0;
                                    output_meta_next = 32'd0;
                                    written_next = 32'd0;
                                    consumed_next = 32'd0;
                                    captured_next = 32'd0;
                                    popped_next = 32'd0;
                                    cycles_next = 64'd0;
                                    frame_remaining_next = 10'd512;
                                    expected_outputs_next = 32'd0;
                                    expected_frame_next = 32'd0;
                                    expected_index_next = 4'd0;
                                    frame_bfp_next = 8'sd0;
                                end
                            end
                            32'd2: begin
                                n_state = S_IDLE;
                                done_next = 1'b0;
                                error_next = 6'd0;
                                core_error_next = 12'd0;
                                clip_start_next = 1'b0;
                                abort_next = 1'b1;
                                pcm_valid_next = 1'b0;
                                pcm_next = 16'sd0;
                                pcm_last_next = 1'b0;
                                output_valid_next = 1'b0;
                                output_data_next = 64'd0;
                                output_frame_next = 32'd0;
                                output_meta_next = 32'd0;
                                written_next = 32'd0;
                                consumed_next = 32'd0;
                                captured_next = 32'd0;
                                popped_next = 32'd0;
                                cycles_next = 64'd0;
                                frame_remaining_next = 10'd512;
                                expected_outputs_next = 32'd0;
                                expected_frame_next = 32'd0;
                                expected_index_next = 4'd0;
                                frame_bfp_next = 8'sd0;
                            end
                            32'd4: begin
                                if (c_state != S_IDLE) begin
                                    b_resp_next = RESP_SLVERR;
                                    error_next[1] = 1'b1;
                                end else begin
                                    done_next = 1'b0;
                                    error_next = 6'd0;
                                    core_error_next = 12'd0;
                                end
                            end
                            default: begin
                                b_resp_next = RESP_SLVERR;
                                error_next[1] = 1'b1;
                            end
                        endcase
                    end
                    16'h0010: begin
                        if ((c_state != S_IDLE) || output_valid_reg || (w_data_reg > MAX_SAMPLES)) begin
                            b_resp_next = RESP_SLVERR;
                            error_next[1] = 1'b1;
                        end else sample_count_next = w_data_reg;
                    end
                    16'h0014: begin
                        if (!pcm_can_write) begin
                            b_resp_next = RESP_SLVERR;
                            error_next[2] = 1'b1;
                        end else begin
                            pcm_valid_next = 1'b1;
                            pcm_next = $signed(w_data_reg[15:0]);
                            written_next = written_reg + 32'd1;
                            pcm_last_next = (written_reg + 32'd1 == sample_count_reg);
                        end
                    end
                    16'h0028: begin
                        if ((w_data_reg != 32'd1) || !output_valid_reg) begin
                            b_resp_next = RESP_SLVERR;
                            error_next[1] = 1'b1;
                        end else begin
                            output_valid_next = 1'b0;
                            popped_next = popped_reg + 32'd1;
                        end
                    end
                    default: begin
                        b_resp_next = RESP_SLVERR;
                        error_next[0] = 1'b1;
                    end
                endcase
            end
        end

        // AR snapshots the old registered state, even with a simultaneous
        // write. The registered R response remains stable until accepted.
        if (s_axi_arvalid && s_axi_arready) begin
            r_valid_next = 1'b1;
            r_resp_next = RESP_OKAY;
            r_data_next = 32'd0;
            if (s_axi_araddr[1:0] != 2'b00) begin
                r_resp_next = RESP_SLVERR;
                error_next[0] = 1'b1;
            end else begin
                case (s_axi_araddr)
                    16'h0000: r_data_next = 32'h4d464343;
                    16'h0004: r_data_next = 32'h00010001;
                    16'h000c: r_data_next = {27'd0, output_valid_reg, pcm_can_write, (|error_reg), done_reg, (c_state == S_BUSY)};
                    16'h0010: r_data_next = sample_count_reg;
                    16'h0018, 16'h001c, 16'h0020, 16'h0024: begin
                        if (!output_valid_reg) begin
                            r_resp_next = RESP_SLVERR;
                            error_next[0] = 1'b1;
                        end else begin
                            case (s_axi_araddr)
                                16'h0018: r_data_next = output_data_reg[31:0];
                                16'h001c: r_data_next = output_data_reg[63:32];
                                16'h0020: r_data_next = output_frame_reg;
                                16'h0024: r_data_next = output_meta_reg;
                                default: r_data_next = 32'd0;
                            endcase
                        end
                    end
                    16'h002c: r_data_next = {26'd0, error_reg};
                    16'h0030: r_data_next = written_reg;
                    16'h0034: r_data_next = consumed_reg;
                    16'h0038: r_data_next = captured_reg;
                    16'h003c: r_data_next = popped_reg;
                    16'h0040: r_data_next = CORE_ID;
                    16'h0044: r_data_next = FORMAT;
                    16'h0048: r_data_next = 32'd512;
                    16'h004c: r_data_next = 32'd160;
                    16'h0050: r_data_next = 32'd13;
                    16'h0054: r_data_next = MAX_SAMPLES;
                    16'h0058: r_data_next = cycles_reg[31:0];
                    16'h005c: r_data_next = cycles_reg[63:32];
                    16'h0060: r_data_next = CONTRACT_TAG;
                    16'h0064: r_data_next = {20'd0, core_error_reg};
                    default: begin
                        r_resp_next = RESP_SLVERR;
                        error_next[0] = 1'b1;
                    end
                endcase
            end
        end
    end
endmodule
`default_nettype wire
