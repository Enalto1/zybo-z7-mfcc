`default_nettype none
// Shared ABI2.0 transport: packed PCM32 in; six-word DDR record out.
// No arithmetic conversion. One input beat and one output record are held.
module mfcc_dma_transport #(
    parameter logic [31:0] CORE_ID = 32'd1,
    parameter logic [31:0] FORMAT = 32'h00011828,
    parameter logic [31:0] CONTRACT_TAG = 32'h283fff8a
) (
    input wire logic s_axi_aclk, input wire logic s_axi_aresetn,
    input wire logic [15:0] s_axi_awaddr, input wire logic [2:0] s_axi_awprot,
    input wire logic s_axi_awvalid, output logic s_axi_awready,
    input wire logic [31:0] s_axi_wdata, input wire logic [3:0] s_axi_wstrb,
    input wire logic s_axi_wvalid, output logic s_axi_wready,
    output logic [1:0] s_axi_bresp, output logic s_axi_bvalid, input wire logic s_axi_bready,
    input wire logic [15:0] s_axi_araddr, input wire logic [2:0] s_axi_arprot,
    input wire logic s_axi_arvalid, output logic s_axi_arready,
    output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp,
    output logic s_axi_rvalid, input wire logic s_axi_rready,
    input wire logic [31:0] s_axis_pcm_tdata, input wire logic [3:0] s_axis_pcm_tkeep,
    input wire logic s_axis_pcm_tlast, input wire logic s_axis_pcm_tvalid, output logic s_axis_pcm_tready,
    output logic [31:0] m_axis_result_tdata, output logic [3:0] m_axis_result_tkeep,
    output logic m_axis_result_tlast, output logic m_axis_result_tvalid, input wire logic m_axis_result_tready,
    output logic irq,
    output logic o_clip_start, output logic o_abort,
    output logic o_pcmvalid, input wire logic i_pcmready,
    output logic signed [15:0] o_pcm, output logic o_pcm_last,
    input wire logic i_outputvalid, output logic o_outputready,
    input wire logic [63:0] i_data64, input wire logic [31:0] i_frame32,
    input wire logic [3:0] i_index4, input wire logic signed [7:0] i_bfp8,
    input wire logic i_last, input wire logic i_error, input wire logic i_metadata_error,
    input wire logic [11:0] i_core_error_detail, input wire logic i_clipdone
);
    typedef enum logic [1:0] {S_IDLE, S_PREP, S_BUSY, S_FINISH} state_t;
    localparam logic [1:0] RESP_OKAY = 2'b00;
    localparam logic [1:0] RESP_SLVERR = 2'b10;
    localparam logic [31:0] MAX_SAMPLES = 32'd262144;
    state_t c_state, n_state;
    logic aw_pending_reg, aw_pending_next;
    logic w_pending_reg, w_pending_next;
    logic [15:0] aw_addr_reg, aw_addr_next;
    logic [31:0] w_data_reg, w_data_next;
    logic [3:0] w_strb_reg, w_strb_next;
    logic b_valid_reg, b_valid_next;
    logic [1:0] b_resp_reg, b_resp_next;
    logic r_valid_reg, r_valid_next;
    logic [1:0] r_resp_reg, r_resp_next;
    logic [31:0] r_data_reg, r_data_next;
    logic [31:0] sample_count_reg, sample_count_next;
    logic done_reg, done_next;
    logic [5:0] error_reg, error_next;
    logic [11:0] core_error_reg, core_error_next;
    logic [1:0] irq_status_reg, irq_status_next;
    logic [1:0] irq_enable_reg, irq_enable_next;
    logic clip_start_reg, clip_start_next;
    logic abort_reg, abort_next;
    logic [31:0] pcm_word_reg, pcm_word_next;
    logic [1:0] pcm_count_reg, pcm_count_next;
    logic pcm_upper_reg, pcm_upper_next;
    logic record_valid_reg, record_valid_next;
    logic [2:0] record_word_reg, record_word_next;
    logic [63:0] record_data_reg, record_data_next;
    logic [31:0] record_frame_reg, record_frame_next;
    logic [3:0] record_index_reg, record_index_next;
    logic signed [7:0] record_bfp_reg, record_bfp_next;
    logic [1:0] record_flags_reg, record_flags_next;
    logic record_final_reg, record_final_next;
    logic [31:0] received_reg, received_next;
    logic [31:0] consumed_reg, consumed_next;
    logic [31:0] captured_reg, captured_next;
    logic [31:0] sent_reg, sent_next;
    logic [31:0] input_bytes_reg, input_bytes_next;
    logic [31:0] output_bytes_reg, output_bytes_next;
    logic [63:0] cycles_reg, cycles_next;
    logic [31:0] expected_records_reg, expected_records_next;
    logic [17:0] prep_remaining_reg, prep_remaining_next;
    logic [31:0] expected_frame_reg, expected_frame_next;
    logic [3:0] expected_index_reg, expected_index_next;
    logic signed [7:0] frame_bfp_reg, frame_bfp_next;
    logic native_done_reg, native_done_next;
    logic core_enabled, input_final, input_odd, malformed_input, bad_record;
    logic [31:0] incoming_samples, incoming_bytes;
    logic [1:0] irq_events;

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
    assign irq = |(irq_status_reg & irq_enable_reg);
    assign core_enabled = s_axi_aresetn && (c_state == S_BUSY) &&
                          !clip_start_reg && !abort_reg && !native_done_reg && !(|error_reg);
    assign s_axis_pcm_tready = core_enabled && (pcm_count_reg == 2'd0) &&
                              (received_reg < sample_count_reg) && !native_done_reg;
    assign o_pcmvalid = core_enabled && (pcm_count_reg != 2'd0);
    assign o_pcm = pcm_upper_reg ? $signed(pcm_word_reg[31:16]) : $signed(pcm_word_reg[15:0]);
    assign o_pcm_last = (clip_start_reg && (sample_count_reg == 32'd0)) ||
                       (o_pcmvalid && (consumed_reg + 32'd1 == sample_count_reg));
    assign o_outputready = core_enabled && !record_valid_reg && !native_done_reg;
    // An already offered record remains valid under stall even if an unrelated
    // error arrives. Errors block new ingress/captures; ABORT cancels the slot.
    assign m_axis_result_tvalid = s_axi_aresetn && record_valid_reg && !abort_reg;
    assign m_axis_result_tkeep = 4'hf;
    assign m_axis_result_tlast = record_final_reg && (record_word_reg == 3'd5);
    assign input_final = (sample_count_reg - received_reg <= 32'd2);
    assign input_odd = (sample_count_reg - received_reg == 32'd1);
    assign malformed_input = (s_axis_pcm_tkeep != (input_odd ? 4'h3 : 4'hf)) ||
                             (s_axis_pcm_tlast != input_final);
    assign incoming_samples = (s_axis_pcm_tkeep == 4'hf) ? 32'd2 :
                              ((s_axis_pcm_tkeep == 4'h3) ? 32'd1 : 32'd0);
    assign incoming_bytes = {31'd0,s_axis_pcm_tkeep[0]} + {31'd0,s_axis_pcm_tkeep[1]} +
                            {31'd0,s_axis_pcm_tkeep[2]} + {31'd0,s_axis_pcm_tkeep[3]};
    assign bad_record = i_metadata_error || (i_frame32 != expected_frame_reg) ||
                        (i_index4 != expected_index_reg) || (i_last != (expected_index_reg == 4'd12)) ||
                        ((expected_index_reg != 4'd0) && (i_bfp8 != frame_bfp_reg)) ||
                        (captured_reg >= expected_records_reg);

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
            irq_status_reg <= 2'd0;
            irq_enable_reg <= 2'd0;
            clip_start_reg <= 1'b0;
            abort_reg <= 1'b0;
            pcm_word_reg <= 32'd0;
            pcm_count_reg <= 2'd0;
            pcm_upper_reg <= 1'b0;
            record_valid_reg <= 1'b0;
            record_word_reg <= 3'd0;
            record_data_reg <= 64'd0;
            record_frame_reg <= 32'd0;
            record_index_reg <= 4'd0;
            record_bfp_reg <= 8'sd0;
            record_flags_reg <= 2'd0;
            record_final_reg <= 1'b0;
            received_reg <= 32'd0;
            consumed_reg <= 32'd0;
            captured_reg <= 32'd0;
            sent_reg <= 32'd0;
            input_bytes_reg <= 32'd0;
            output_bytes_reg <= 32'd0;
            cycles_reg <= 64'd0;
            expected_records_reg <= 32'd0;
            prep_remaining_reg <= 18'd0;
            expected_frame_reg <= 32'd0;
            expected_index_reg <= 4'd0;
            frame_bfp_reg <= 8'sd0;
            native_done_reg <= 1'b0;
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
            irq_status_reg <= irq_status_next;
            irq_enable_reg <= irq_enable_next;
            clip_start_reg <= clip_start_next;
            abort_reg <= abort_next;
            pcm_word_reg <= pcm_word_next;
            pcm_count_reg <= pcm_count_next;
            pcm_upper_reg <= pcm_upper_next;
            record_valid_reg <= record_valid_next;
            record_word_reg <= record_word_next;
            record_data_reg <= record_data_next;
            record_frame_reg <= record_frame_next;
            record_index_reg <= record_index_next;
            record_bfp_reg <= record_bfp_next;
            record_flags_reg <= record_flags_next;
            record_final_reg <= record_final_next;
            received_reg <= received_next;
            consumed_reg <= consumed_next;
            captured_reg <= captured_next;
            sent_reg <= sent_next;
            input_bytes_reg <= input_bytes_next;
            output_bytes_reg <= output_bytes_next;
            cycles_reg <= cycles_next;
            expected_records_reg <= expected_records_next;
            prep_remaining_reg <= prep_remaining_next;
            expected_frame_reg <= expected_frame_next;
            expected_index_reg <= expected_index_next;
            frame_bfp_reg <= frame_bfp_next;
            native_done_reg <= native_done_next;
        end
    end

    always_comb begin
        n_state = c_state;
        aw_pending_next = aw_pending_reg;
        w_pending_next = w_pending_reg;
        aw_addr_next = aw_addr_reg;
        w_data_next = w_data_reg;
        w_strb_next = w_strb_reg;
        b_valid_next = b_valid_reg && !s_axi_bready;
        b_resp_next = b_resp_reg;
        r_valid_next = r_valid_reg && !s_axi_rready;
        r_resp_next = r_resp_reg;
        r_data_next = r_data_reg;
        sample_count_next = sample_count_reg;
        done_next = done_reg;
        error_next = error_reg;
        core_error_next = core_error_reg;
        irq_status_next = irq_status_reg;
        irq_enable_next = irq_enable_reg;
        clip_start_next = 1'b0;
        abort_next = 1'b0;
        pcm_word_next = pcm_word_reg;
        pcm_count_next = pcm_count_reg;
        pcm_upper_next = pcm_upper_reg;
        record_valid_next = record_valid_reg;
        record_word_next = record_word_reg;
        record_data_next = record_data_reg;
        record_frame_next = record_frame_reg;
        record_index_next = record_index_reg;
        record_bfp_next = record_bfp_reg;
        record_flags_next = record_flags_reg;
        record_final_next = record_final_reg;
        received_next = received_reg;
        consumed_next = consumed_reg;
        captured_next = captured_reg;
        sent_next = sent_reg;
        input_bytes_next = input_bytes_reg;
        output_bytes_next = output_bytes_reg;
        cycles_next = cycles_reg;
        expected_records_next = expected_records_reg;
        prep_remaining_next = prep_remaining_reg;
        expected_frame_next = expected_frame_reg;
        expected_index_next = expected_index_reg;
        frame_bfp_next = frame_bfp_reg;
        native_done_next = native_done_reg;
        irq_events = 2'd0;
        m_axis_result_tdata = 32'd0;
        case (record_word_reg)
            3'd0: m_axis_result_tdata = record_data_reg[31:0];
            3'd1: m_axis_result_tdata = record_data_reg[63:32];
            3'd2: m_axis_result_tdata = record_frame_reg;
            3'd3: m_axis_result_tdata = {28'd0,record_index_reg};
            3'd4: m_axis_result_tdata = {{24{record_bfp_reg[7]}},record_bfp_reg};
            3'd5: m_axis_result_tdata = {30'd0,record_flags_reg};
            default: m_axis_result_tdata = 32'd0;
        endcase
        if (s_axi_awvalid && s_axi_awready) begin
            aw_pending_next = 1'b1;
            aw_addr_next = s_axi_awaddr;
        end
        if (s_axi_wvalid && s_axi_wready) begin
            w_pending_next = 1'b1;
            w_data_next = s_axi_wdata;
            w_strb_next = s_axi_wstrb;
        end
        if (m_axis_result_tvalid && m_axis_result_tready) begin
            output_bytes_next = output_bytes_reg + 32'd4;
            if (record_word_reg == 3'd5) begin
                record_valid_next = 1'b0;
                record_word_next = 3'd0;
                sent_next = sent_reg + 32'd1;
            end else record_word_next = record_word_reg + 3'd1;
        end
        // Continue to capture a first native fault while the completed record
        // drains. Sticky diagnostics do not recreate IRQ events after W1C.
        if (((c_state == S_BUSY) || (c_state == S_FINISH)) &&
            !clip_start_reg && !abort_reg && !(|error_reg)) begin
            if (i_error) begin error_next[3] = 1'b1; irq_events[1] = 1'b1; end
            if (i_metadata_error) begin error_next[4] = 1'b1; irq_events[1] = 1'b1; end
            if (core_error_reg == 12'd0) core_error_next = i_core_error_detail;
        end
        case (c_state)
            S_IDLE: begin end
            S_PREP: begin
                // Derive 13 * (1 + floor((N-512)/160)) without a long
                // combinational divider. The configured maximum leaves an
                // 18-bit remainder and at most 1635 subtract iterations.
                // Preparation is busy time; no native/AXIS handshakes occur.
                cycles_next = cycles_reg + 64'd1;
                if (!(|error_reg)) begin
                    if (prep_remaining_reg >= 18'd160) begin
                        prep_remaining_next = prep_remaining_reg - 18'd160;
                        expected_records_next = expected_records_reg + 32'd13;
                    end else begin
                        n_state = S_BUSY;
                        clip_start_next = 1'b1;
                    end
                end
            end
            S_BUSY: begin
                cycles_next = cycles_reg + 64'd1;
                if (core_enabled) begin
                    if (s_axis_pcm_tvalid && s_axis_pcm_tready) begin
                        input_bytes_next = input_bytes_reg + incoming_bytes;
                        received_next = received_reg + incoming_samples;
                        if (malformed_input) begin error_next[2] = 1'b1; irq_events[1] = 1'b1; end
                        else begin
                            pcm_word_next = s_axis_pcm_tdata;
                            pcm_count_next = input_odd ? 2'd1 : 2'd2;
                            pcm_upper_next = 1'b0;
                        end
                    end
                    if (o_pcmvalid && i_pcmready) begin
                        pcm_count_next = pcm_count_reg - 2'd1;
                        pcm_upper_next = 1'b1;
                        consumed_next = consumed_reg + 32'd1;
                    end
                    if (i_outputvalid && o_outputready) begin
                        record_valid_next = 1'b1;
                        record_word_next = 3'd0;
                        record_data_next = i_data64;
                        record_frame_next = i_frame32;
                        record_index_next = i_index4;
                        record_bfp_next = i_bfp8;
                        record_flags_next = {(i_error || i_metadata_error), i_last};
                        record_final_next = (captured_reg + 32'd1 == expected_records_reg);
                        captured_next = captured_reg + 32'd1;
                        if (bad_record) begin error_next[4] = 1'b1; irq_events[1] = 1'b1; end
                        if (expected_index_reg == 4'd0) frame_bfp_next = i_bfp8;
                        if (expected_index_reg == 4'd12) begin
                            expected_index_next = 4'd0;
                            expected_frame_next = expected_frame_reg + 32'd1;
                        end else expected_index_next = expected_index_reg + 4'd1;
                    end
                end
                // Native done may coincide with the last PCM/record handshake.
                // Its registered value masks all new stream acceptance and
                // enters a separate finish phase; live input parsing cannot
                // drive the state transition through combinational errors.
                if (i_clipdone && !abort_reg) native_done_next = 1'b1;
                if (native_done_reg) n_state = S_FINISH;
            end
            S_FINISH: begin
                cycles_next = cycles_reg + 64'd1;
                // Handshake counters have committed before this phase. The
                // captured record still serializes outside the state case.
                // Validate its final word on the clock after it is accepted.
                if (!(|error_reg)) begin
                    if ((received_reg != sample_count_reg) || (consumed_reg != sample_count_reg) ||
                        (captured_reg != expected_records_reg) || (pcm_count_reg != 2'd0))
                        begin error_next[5] = 1'b1; irq_events[1] = 1'b1; end
                    else if (!record_valid_reg) begin
                        if ((sent_reg != expected_records_reg) ||
                            (input_bytes_reg != (sample_count_reg << 1)) ||
                            (output_bytes_reg != expected_records_reg * 32'd24)) begin error_next[5] = 1'b1; irq_events[1] = 1'b1; end
                        else begin
                            done_next = 1'b1;
                            n_state = S_IDLE;
                            irq_events[0] = 1'b1;
                        end
                    end
                end
            end
            default: begin
                n_state = S_IDLE;
                abort_next = 1'b1;
                done_next = 1'b0;
                pcm_count_next = 2'd0;
                record_valid_next = 1'b0;
                begin error_next[5] = 1'b1; irq_events[1] = 1'b1; end
            end
        endcase
        // Registered AW and W may arrive independently; commit only once.
        if (aw_pending_reg && w_pending_reg && !b_valid_reg) begin
            aw_pending_next = 1'b0;
            w_pending_next = 1'b0;
            b_valid_next = 1'b1;
            b_resp_next = RESP_OKAY;
            if ((aw_addr_reg[1:0] != 2'b00) || (w_strb_reg != 4'hf)) begin
                b_resp_next = RESP_SLVERR;
                begin error_next[0] = 1'b1; irq_events[1] = 1'b1; end
            end else begin
                case (aw_addr_reg)
                    16'h0008: begin
                        case (w_data_reg)
                            32'd1: begin
                                if ((c_state != S_IDLE) || record_valid_reg) begin
                                    b_resp_next = RESP_SLVERR;
                                    begin error_next[1] = 1'b1; irq_events[1] = 1'b1; end
                                end else begin
                                    n_state = S_PREP;
                                    clip_start_next = 1'b0;
                                    abort_next = 1'b0;
                                    if (sample_count_reg < 32'd512) begin
                                        expected_records_next = 32'd0;
                                        prep_remaining_next = 18'd0;
                                    end else begin
                                        expected_records_next = 32'd13;
                                        prep_remaining_next = 18'(sample_count_reg - 32'd512);
                                    end
                                    done_next = 1'b0;
                                    error_next = 6'd0;
                                    core_error_next = 12'd0;
                                    irq_status_next = 2'd0;
                                    pcm_word_next = 32'd0;
                                    pcm_count_next = 2'd0;
                                    pcm_upper_next = 1'b0;
                                    record_valid_next = 1'b0;
                                    record_word_next = 3'd0;
                                    record_data_next = 64'd0;
                                    record_frame_next = 32'd0;
                                    record_index_next = 4'd0;
                                    record_bfp_next = 8'sd0;
                                    record_flags_next = 2'd0;
                                    record_final_next = 1'b0;
                                    received_next = 32'd0;
                                    consumed_next = 32'd0;
                                    captured_next = 32'd0;
                                    sent_next = 32'd0;
                                    input_bytes_next = 32'd0;
                                    output_bytes_next = 32'd0;
                                    cycles_next = 64'd0;
                                    expected_frame_next = 32'd0;
                                    expected_index_next = 4'd0;
                                    frame_bfp_next = 8'sd0;
                                    native_done_next = 1'b0;
                                    irq_events = 2'd0;
                                end
                            end
                            32'd2: begin
                                n_state = S_IDLE;
                                clip_start_next = 1'b0;
                                abort_next = 1'b1;
                                done_next = 1'b0;
                                error_next = 6'd0;
                                core_error_next = 12'd0;
                                irq_status_next = 2'd0;
                                pcm_word_next = 32'd0;
                                pcm_count_next = 2'd0;
                                pcm_upper_next = 1'b0;
                                record_valid_next = 1'b0;
                                record_word_next = 3'd0;
                                record_data_next = 64'd0;
                                record_frame_next = 32'd0;
                                record_index_next = 4'd0;
                                record_bfp_next = 8'sd0;
                                record_flags_next = 2'd0;
                                record_final_next = 1'b0;
                                received_next = 32'd0;
                                consumed_next = 32'd0;
                                captured_next = 32'd0;
                                sent_next = 32'd0;
                                input_bytes_next = 32'd0;
                                output_bytes_next = 32'd0;
                                cycles_next = 64'd0;
                                expected_records_next = 32'd0;
                                prep_remaining_next = 18'd0;
                                expected_frame_next = 32'd0;
                                expected_index_next = 4'd0;
                                frame_bfp_next = 8'sd0;
                                native_done_next = 1'b0;
                                irq_events = 2'd0;
                            end
                            32'd4: begin
                                if (c_state != S_IDLE) begin
                                    b_resp_next = RESP_SLVERR;
                                    begin error_next[1] = 1'b1; irq_events[1] = 1'b1; end
                                end else begin
                                    done_next = 1'b0;
                                    error_next = 6'd0;
                                    core_error_next = 12'd0;
                                    irq_status_next = 2'd0;
                                    irq_events = 2'd0;
                                end
                            end
                            default: begin
                                b_resp_next = RESP_SLVERR;
                                begin error_next[1] = 1'b1; irq_events[1] = 1'b1; end
                            end
                        endcase
                    end
                    16'h0010: begin
                        if ((c_state != S_IDLE) || (w_data_reg > MAX_SAMPLES)) begin
                            b_resp_next = RESP_SLVERR;
                            begin error_next[1] = 1'b1; irq_events[1] = 1'b1; end
                        end else sample_count_next = w_data_reg;
                    end
                    16'h0070: begin
                        if (|w_data_reg[31:2]) begin
                            b_resp_next = RESP_SLVERR;
                            begin error_next[1] = 1'b1; irq_events[1] = 1'b1; end
                        end else irq_status_next = irq_status_reg & ~w_data_reg[1:0];
                    end
                    16'h0074: begin
                        if (|w_data_reg[31:2]) begin
                            b_resp_next = RESP_SLVERR;
                            begin error_next[1] = 1'b1; irq_events[1] = 1'b1; end
                        end else irq_enable_next = w_data_reg[1:0];
                    end
                    default: begin
                        b_resp_next = RESP_SLVERR;
                        begin error_next[0] = 1'b1; irq_events[1] = 1'b1; end
                    end
                endcase
            end
        end
        // AR snapshots registered state. A bad read wins over command clearing.
        if (s_axi_arvalid && s_axi_arready) begin
            r_valid_next = 1'b1;
            r_resp_next = RESP_OKAY;
            r_data_next = 32'd0;
            if (s_axi_araddr[1:0] != 2'b00) begin
                r_resp_next = RESP_SLVERR;
                begin error_next[0] = 1'b1; irq_events[1] = 1'b1; end
            end else begin
                case (s_axi_araddr)
                    16'h0000: r_data_next = 32'h4d464343;
                    16'h0004: r_data_next = 32'h00020000;
                    16'h000c: r_data_next = {29'd0,(|error_reg),done_reg,(c_state != S_IDLE)};
                    16'h0010: r_data_next = sample_count_reg;
                    16'h002c: r_data_next = {26'd0,error_reg};
                    16'h0030: r_data_next = received_reg;
                    16'h0034: r_data_next = consumed_reg;
                    16'h0038: r_data_next = captured_reg;
                    16'h003c: r_data_next = sent_reg;
                    16'h0040: r_data_next = CORE_ID;
                    16'h0044: r_data_next = FORMAT;
                    16'h0048: r_data_next = 32'd512;
                    16'h004c: r_data_next = 32'd160;
                    16'h0050: r_data_next = 32'd13;
                    16'h0054: r_data_next = MAX_SAMPLES;
                    16'h0058: r_data_next = cycles_reg[31:0];
                    16'h005c: r_data_next = cycles_reg[63:32];
                    16'h0060: r_data_next = CONTRACT_TAG;
                    16'h0064: r_data_next = {20'd0,core_error_reg};
                    16'h0068: r_data_next = input_bytes_reg;
                    16'h006c: r_data_next = output_bytes_reg;
                    16'h0070: r_data_next = {30'd0,irq_status_reg};
                    16'h0074: r_data_next = {30'd0,irq_enable_reg};
                    default: begin
                        r_resp_next = RESP_SLVERR;
                        begin error_next[0] = 1'b1; irq_events[1] = 1'b1; end
                    end
                endcase
            end
        end
        // New fault events win over W1C. Sticky diagnostics alone do not
        // continuously recreate the event after acknowledgement.
        if (((c_state == S_PREP) || (c_state == S_FINISH)) &&
            !abort_next && (|error_next)) begin
            n_state = c_state;
            clip_start_next = 1'b0;
            done_next = 1'b0;
            irq_events[0] = 1'b0;
        end
        irq_status_next = irq_status_next | irq_events;
    end
endmodule
`default_nettype wire

