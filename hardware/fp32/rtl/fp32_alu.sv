module fp32_alu #(
    parameter bit INCLUDE_LOG = 1'b1
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        req_valid,
    output logic        req_ready,
    input  logic [1:0]  req_op,
    input  logic [31:0] req_a,
    input  logic [31:0] req_b,
    output logic        rsp_valid,
    input  logic        rsp_ready,
    output logic [31:0] rsp_data,
    output logic        o_error
);
    typedef enum logic [2:0] {S_IDLE, S_ISSUE, S_WAIT, S_HOLD, S_FAULT} state_t;
    localparam logic [1:0] OP_MUL = 2'b00;
    localparam logic [1:0] OP_ADD = 2'b01;
    localparam logic [1:0] OP_SUB = 2'b10;
    localparam logic [1:0] OP_LOG = 2'b11;

    state_t c_state, n_state;
    logic [1:0] op_reg, op_next;
    logic [31:0] a_reg, a_next, b_reg, b_next;
    logic a_sent_reg, a_sent_next, b_sent_reg, b_sent_next;
    logic operation_sent_reg, operation_sent_next;
    logic [31:0] response_reg, response_next;
    logic response_valid_reg, response_valid_next, error_reg, error_next;
    logic reset_recovery_reg, reset_recovery_next;
    logic mul_aclken, add_aclken, log_aclken;

    logic mul_a_valid, mul_b_valid, mul_a_ready, mul_b_ready;
    logic mul_result_valid, mul_result_ready;
    logic [31:0] mul_result_data;
    logic add_a_valid, add_b_valid, add_a_ready, add_b_ready;
    logic add_operation_valid, add_operation_ready;
    logic [7:0] add_operation_data;
    logic add_result_valid, add_result_ready;
    logic [31:0] add_result_data;
    logic log_a_valid, log_a_ready, log_result_valid, log_result_ready;
    logic [31:0] log_result_data;

    assign rsp_valid = response_valid_reg;
    assign rsp_data = response_reg;
    assign o_error = error_reg;
    // Vendor clock enables pause the actual IP; the clock itself is never gated.
    // Keep one enabled idle clock after synchronous reset is released (PG060).
    assign mul_aclken = !rst_n || reset_recovery_reg ||
        ((c_state == S_ISSUE || c_state == S_WAIT) && op_reg == OP_MUL);
    assign add_aclken = !rst_n || reset_recovery_reg ||
        ((c_state == S_ISSUE || c_state == S_WAIT) && (op_reg == OP_ADD || op_reg == OP_SUB));
    assign log_aclken = !rst_n || reset_recovery_reg ||
        ((c_state == S_ISSUE || c_state == S_WAIT) && op_reg == OP_LOG);

    fp32_mul U_MUL (
        .aclk(clk), .aresetn(rst_n), .aclken(mul_aclken),
        .s_axis_a_tvalid(mul_a_valid), .s_axis_a_tready(mul_a_ready), .s_axis_a_tdata(a_reg),
        .s_axis_b_tvalid(mul_b_valid), .s_axis_b_tready(mul_b_ready), .s_axis_b_tdata(b_reg),
        .m_axis_result_tvalid(mul_result_valid), .m_axis_result_tready(mul_result_ready),
        .m_axis_result_tdata(mul_result_data)
    );
    fp32_addsub U_ADDSUB (
        .aclk(clk), .aresetn(rst_n), .aclken(add_aclken),
        .s_axis_a_tvalid(add_a_valid), .s_axis_a_tready(add_a_ready), .s_axis_a_tdata(a_reg),
        .s_axis_b_tvalid(add_b_valid), .s_axis_b_tready(add_b_ready), .s_axis_b_tdata(b_reg),
        .s_axis_operation_tvalid(add_operation_valid), .s_axis_operation_tready(add_operation_ready),
        .s_axis_operation_tdata(add_operation_data),
        .m_axis_result_tvalid(add_result_valid), .m_axis_result_tready(add_result_ready),
        .m_axis_result_tdata(add_result_data)
    );
    generate
        if (INCLUDE_LOG) begin : G_LOG
            fp32_log U_LOG (
                .aclk(clk), .aresetn(rst_n), .aclken(log_aclken),
                .s_axis_a_tvalid(log_a_valid), .s_axis_a_tready(log_a_ready), .s_axis_a_tdata(a_reg),
                .m_axis_result_tvalid(log_result_valid), .m_axis_result_tready(log_result_ready),
                .m_axis_result_tdata(log_result_data)
            );
        end else begin : G_NO_LOG
            assign log_a_ready = 1'b0;
            assign log_result_valid = 1'b0;
            assign log_result_data = 32'b0;
        end
    endgenerate

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE;
            op_reg <= OP_MUL;
            a_reg <= 32'b0;
            b_reg <= 32'b0;
            a_sent_reg <= 1'b0;
            b_sent_reg <= 1'b0;
            operation_sent_reg <= 1'b0;
            response_reg <= 32'b0;
            response_valid_reg <= 1'b0;
            error_reg <= 1'b0;
            reset_recovery_reg <= 1'b1;
        end else begin
            c_state <= n_state;
            op_reg <= op_next;
            a_reg <= a_next;
            b_reg <= b_next;
            a_sent_reg <= a_sent_next;
            b_sent_reg <= b_sent_next;
            operation_sent_reg <= operation_sent_next;
            response_reg <= response_next;
            response_valid_reg <= response_valid_next;
            error_reg <= error_next;
            reset_recovery_reg <= reset_recovery_next;
        end
    end

    always_comb begin
        n_state = c_state;
        op_next = op_reg;
        a_next = a_reg;
        b_next = b_reg;
        a_sent_next = a_sent_reg;
        b_sent_next = b_sent_reg;
        operation_sent_next = operation_sent_reg;
        response_next = response_reg;
        response_valid_next = response_valid_reg;
        error_next = error_reg;
        reset_recovery_next = 1'b0;
        req_ready = 1'b0;
        mul_a_valid = 1'b0;
        mul_b_valid = 1'b0;
        mul_result_ready = 1'b0;
        add_a_valid = 1'b0;
        add_b_valid = 1'b0;
        add_operation_valid = 1'b0;
        add_operation_data = op_reg == OP_SUB ? 8'h01 : 8'h00;
        add_result_ready = 1'b0;
        log_a_valid = 1'b0;
        log_result_ready = 1'b0;
        case (c_state)
            S_IDLE: begin
                req_ready = rst_n && !reset_recovery_reg && !error_reg;
                if (req_valid && req_ready) begin
                    op_next = req_op;
                    a_next = req_a;
                    b_next = req_b;
                    a_sent_next = 1'b0;
                    b_sent_next = req_op == OP_LOG;
                    operation_sent_next = req_op == OP_MUL || req_op == OP_LOG;
                    if (req_op == OP_LOG && !INCLUDE_LOG) begin
                        error_next = 1'b1;
                        response_next = 32'h7fc00000;
                        response_valid_next = 1'b1;
                        n_state = S_HOLD;
                    end else begin
                        n_state = S_ISSUE;
                    end
                end
            end
            S_ISSUE: begin
                case (op_reg)
                    OP_MUL: begin
                        mul_a_valid = !a_sent_reg;
                        mul_b_valid = !b_sent_reg;
                        if (mul_a_valid && mul_a_ready) a_sent_next = 1'b1;
                        if (mul_b_valid && mul_b_ready) b_sent_next = 1'b1;
                    end
                    OP_ADD, OP_SUB: begin
                        add_a_valid = !a_sent_reg;
                        add_b_valid = !b_sent_reg;
                        add_operation_valid = !operation_sent_reg;
                        if (add_a_valid && add_a_ready) a_sent_next = 1'b1;
                        if (add_b_valid && add_b_ready) b_sent_next = 1'b1;
                        if (add_operation_valid && add_operation_ready) operation_sent_next = 1'b1;
                    end
                    OP_LOG: begin
                        log_a_valid = !a_sent_reg;
                        if (log_a_valid && log_a_ready) a_sent_next = 1'b1;
                    end
                    default: begin
                        error_next = 1'b1;
                        n_state = S_FAULT;
                    end
                endcase
                if (a_sent_next && b_sent_next && operation_sent_next) n_state = S_WAIT;
            end
            S_WAIT: begin
                case (op_reg)
                    OP_MUL: begin
                        mul_result_ready = 1'b1;
                        if (mul_result_valid) begin
                            response_next = mul_result_data;
                            response_valid_next = 1'b1;
                            n_state = S_HOLD;
                        end
                    end
                    OP_ADD, OP_SUB: begin
                        add_result_ready = 1'b1;
                        if (add_result_valid) begin
                            response_next = add_result_data;
                            response_valid_next = 1'b1;
                            n_state = S_HOLD;
                        end
                    end
                    OP_LOG: begin
                        log_result_ready = 1'b1;
                        if (log_result_valid) begin
                            response_next = log_result_data;
                            response_valid_next = 1'b1;
                            n_state = S_HOLD;
                        end
                    end
                    default: begin
                        error_next = 1'b1;
                        n_state = S_FAULT;
                    end
                endcase
            end
            S_HOLD: begin
                if (rsp_ready && response_valid_reg) begin
                    response_valid_next = 1'b0;
                    n_state = error_reg ? S_FAULT : S_IDLE;
                end
            end
            S_FAULT: begin
                response_valid_next = 1'b0;
                error_next = 1'b1;
            end
            default: begin
                response_valid_next = 1'b0;
                error_next = 1'b1;
                n_state = S_FAULT;
            end
        endcase
    end
endmodule
