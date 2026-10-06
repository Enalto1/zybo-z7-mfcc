// One coefficient ROM access and one genuine vendor float multiply per sample.
module fp32_window (
    input logic clk, input logic rst_n,
    input logic s_valid, output logic s_ready, input logic [31:0] s_data,
    input logic [8:0] s_index, input logic [31:0] s_frame_id,
    input logic [31:0] s_start_sample, input logic s_last,
    output logic m_valid, input logic m_ready, output logic [31:0] m_data,
    output logic [8:0] m_index, output logic [31:0] m_frame_id,
    output logic [31:0] m_start_sample, output logic m_last,
    output logic o_busy, output logic o_error
);
    typedef enum logic [2:0] {S_IDLE, S_READ, S_WAIT1, S_WAIT2,
        S_MULTIPLY, S_RESULT, S_OUTPUT} state_t;
    state_t c_state, n_state;
    logic [31:0] sample_reg, sample_next, coeff_reg, coeff_next, data_reg, data_next;
    logic [31:0] frame_reg, frame_next, start_reg, start_next;
    logic [8:0] index_reg, index_next;
    logic last_reg, last_next, error_reg, error_next;
    logic rom_en, req_valid, req_ready, rsp_valid, rsp_ready, alu_error;
    logic [31:0] rom_data, rsp_data;
    assign m_data = data_reg;
    assign m_index = index_reg;
    assign m_frame_id = frame_reg;
    assign m_start_sample = start_reg;
    assign m_last = last_reg;
    assign o_busy = (c_state != S_IDLE);
    assign o_error = error_reg | alu_error;
    xpm_memory_sprom #(
        .ADDR_WIDTH_A(9), .AUTO_SLEEP_TIME(0), .CASCADE_HEIGHT(0),
        .ECC_MODE("no_ecc"), .MEMORY_INIT_FILE("window.mem"), .MEMORY_INIT_PARAM("0"),
        .MEMORY_OPTIMIZATION("true"), .MEMORY_PRIMITIVE("block"), .MEMORY_SIZE(16384),
        .MESSAGE_CONTROL(0), .READ_DATA_WIDTH_A(32), .READ_LATENCY_A(2),
        .READ_RESET_VALUE_A("0"), .RST_MODE_A("SYNC"), .SIM_ASSERT_CHK(1),
        .USE_MEM_INIT(1), .WAKEUP_TIME("disable_sleep")
    ) U_WINDOW_ROM (
        .clka(clk), .rsta(!rst_n), .ena(rom_en), .regcea(1'b1), .addra(index_reg),
        .douta(rom_data), .sleep(1'b0), .injectsbiterra(1'b0), .injectdbiterra(1'b0),
        .sbiterra(), .dbiterra()
    );
    fp32_alu #(.INCLUDE_LOG(1'b0)) U_ALU (
        .clk(clk), .rst_n(rst_n), .req_valid(req_valid), .req_ready(req_ready),
        .req_op(2'b00), .req_a(sample_reg), .req_b(coeff_reg),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_data(rsp_data), .o_error(alu_error)
    );
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE;
            sample_reg <= 32'd0;
            coeff_reg <= 32'd0;
            data_reg <= 32'd0;
            frame_reg <= 32'd0;
            start_reg <= 32'd0;
            index_reg <= 9'd0;
            last_reg <= 1'b0;
            error_reg <= 1'b0;
        end else begin
            c_state <= n_state;
            sample_reg <= sample_next;
            coeff_reg <= coeff_next;
            data_reg <= data_next;
            frame_reg <= frame_next;
            start_reg <= start_next;
            index_reg <= index_next;
            last_reg <= last_next;
            error_reg <= error_next;
        end
    end
    always_comb begin
        n_state = c_state;
        sample_next = sample_reg;
        coeff_next = coeff_reg;
        data_next = data_reg;
        frame_next = frame_reg;
        start_next = start_reg;
        index_next = index_reg;
        last_next = last_reg;
        error_next = error_reg;
        s_ready = 1'b0;
        m_valid = 1'b0;
        rom_en = 1'b0;
        req_valid = 1'b0;
        rsp_ready = 1'b0;
        case (c_state)
            S_IDLE: begin
                s_ready = !error_reg;
                if (s_valid && s_ready) begin
                    sample_next = s_data;
                    index_next = s_index;
                    frame_next = s_frame_id;
                    start_next = s_start_sample;
                    last_next = s_last;
                    if (s_last != (s_index == 9'd511)) error_next = 1'b1;
                    n_state = S_READ;
                end
            end
            S_READ: begin rom_en = 1'b1; n_state = S_WAIT1; end
            S_WAIT1: n_state = S_WAIT2;
            S_WAIT2: begin coeff_next = rom_data; n_state = S_MULTIPLY; end
            S_MULTIPLY: begin
                req_valid = 1'b1;
                if (req_ready) n_state = S_RESULT;
            end
            S_RESULT: begin
                rsp_ready = 1'b1;
                if (rsp_valid) begin
                    data_next = rsp_data;
                    if (rsp_data[30:23] == 8'hff) error_next = 1'b1;
                    n_state = S_OUTPUT;
                end
            end
            S_OUTPUT: begin m_valid = 1'b1; if (m_ready) n_state = S_IDLE; end
            default: begin error_next = 1'b1; n_state = S_IDLE; end
        endcase
    end
endmodule
