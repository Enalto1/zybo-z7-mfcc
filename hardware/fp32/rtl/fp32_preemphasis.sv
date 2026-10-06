// One accepted PCM sample -> one filtered sample. State resets at clip start only.
module fp32_preemphasis (
    input logic clk, input logic rst_n, input logic clip_clear,
    input logic s_valid, output logic s_ready, input logic [15:0] s_pcm,
    output logic m_valid, input logic m_ready, output logic [31:0] m_data,
    output logic o_busy, output logic o_error,
    output logic dbg_input_valid, output logic [31:0] dbg_input_data
);
    typedef enum logic [3:0] {S_IDLE, S_CONV_SEND, S_CONV_WAIT,
        S_MUL_SEND, S_MUL_WAIT, S_SUB_SEND, S_SUB_WAIT, S_OUTPUT} state_t;
    state_t c_state, n_state;
    logic [15:0] pcm_reg, pcm_next;
    logic [31:0] previous_reg, previous_next, current_reg, current_next;
    logic [31:0] product_reg, product_next, result_reg, result_next;
    logic error_reg, error_next, input_pulse_reg, input_pulse_next;
    logic recovery_reg, recovery_next, conv_enable;
    logic conv_s_valid, conv_s_ready, conv_m_valid, conv_m_ready;
    logic [31:0] conv_data;
    logic req_valid, req_ready, rsp_valid, rsp_ready, alu_error;
    logic [1:0] req_op;
    logic [31:0] req_a, req_b, rsp_data;

    assign m_data = result_reg;
    assign o_busy = (c_state != S_IDLE);
    assign o_error = error_reg | alu_error;
    assign dbg_input_valid = input_pulse_reg;
    assign dbg_input_data = current_reg;
    // Vendor clock enable holds the converter while another operator works.
    assign conv_enable = !rst_n || recovery_reg || (c_state == S_CONV_SEND) || (c_state == S_CONV_WAIT);

    fp32_pcm16 U_PCM16 (
        .aclk(clk), .aresetn(rst_n), .aclken(conv_enable),
        .s_axis_a_tvalid(conv_s_valid), .s_axis_a_tready(conv_s_ready),
        .s_axis_a_tdata(pcm_reg), .m_axis_result_tvalid(conv_m_valid),
        .m_axis_result_tready(conv_m_ready), .m_axis_result_tdata(conv_data)
    );
    fp32_alu #(.INCLUDE_LOG(1'b0)) U_ALU (
        .clk(clk), .rst_n(rst_n), .req_valid(req_valid), .req_ready(req_ready),
        .req_op(req_op), .req_a(req_a), .req_b(req_b),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_data(rsp_data), .o_error(alu_error)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE;
            pcm_reg <= 16'd0;
            previous_reg <= 32'd0;
            current_reg <= 32'd0;
            product_reg <= 32'd0;
            result_reg <= 32'd0;
            error_reg <= 1'b0;
            input_pulse_reg <= 1'b0;
            recovery_reg <= 1'b1;
        end else begin
            c_state <= n_state;
            pcm_reg <= pcm_next;
            previous_reg <= previous_next;
            current_reg <= current_next;
            product_reg <= product_next;
            result_reg <= result_next;
            error_reg <= error_next;
            input_pulse_reg <= input_pulse_next;
            recovery_reg <= recovery_next;
        end
    end
    always_comb begin
        n_state = c_state;
        pcm_next = pcm_reg;
        previous_next = previous_reg;
        current_next = current_reg;
        product_next = product_reg;
        result_next = result_reg;
        error_next = error_reg;
        input_pulse_next = 1'b0;
        recovery_next = 1'b0;
        s_ready = 1'b0;
        m_valid = 1'b0;
        conv_s_valid = 1'b0;
        conv_m_ready = 1'b0;
        req_valid = 1'b0;
        req_op = 2'b00;
        req_a = previous_reg;
        req_b = 32'h3f733333; // binary32(0.95), separately rounded multiply
        rsp_ready = 1'b0;
        case (c_state)
            S_IDLE: begin
                s_ready = rst_n && !recovery_reg && !clip_clear && !error_reg;
                if (s_valid && s_ready) begin
                    pcm_next = s_pcm;
                    n_state = S_CONV_SEND;
                end
            end
            S_CONV_SEND: begin
                conv_s_valid = 1'b1;
                if (conv_s_ready) n_state = S_CONV_WAIT;
            end
            S_CONV_WAIT: begin
                conv_m_ready = 1'b1;
                if (conv_m_valid) begin
                    current_next = conv_data;
                    input_pulse_next = 1'b1;
                    n_state = S_MUL_SEND;
                end
            end
            S_MUL_SEND: begin
                req_valid = 1'b1;
                if (req_ready) n_state = S_MUL_WAIT;
            end
            S_MUL_WAIT: begin
                rsp_ready = 1'b1;
                if (rsp_valid) begin
                    product_next = rsp_data;
                    n_state = S_SUB_SEND;
                end
            end
            S_SUB_SEND: begin
                req_valid = 1'b1;
                req_op = 2'b10;
                req_a = current_reg;
                req_b = product_reg;
                if (req_ready) n_state = S_SUB_WAIT;
            end
            S_SUB_WAIT: begin
                rsp_ready = 1'b1;
                if (rsp_valid) begin
                    result_next = rsp_data;
                    if (rsp_data[30:23] == 8'hff) error_next = 1'b1;
                    n_state = S_OUTPUT;
                end
            end
            S_OUTPUT: begin
                m_valid = 1'b1;
                if (m_ready) begin
                    previous_next = current_reg;
                    n_state = S_IDLE;
                end
            end
            default: begin
                error_next = 1'b1;
                n_state = S_IDLE;
            end
        endcase
        if (clip_clear) begin
            // Top only asserts this when idle: no in-flight IP transaction is discarded.
            previous_next = 32'd0;
            if (c_state != S_IDLE) error_next = 1'b1;
        end
    end
endmodule
