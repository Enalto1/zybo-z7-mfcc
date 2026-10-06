module fp32_backend #(
    parameter MEL_INIT_FILE = "mel_compact.mem",
    parameter MEL_DESCRIPTOR_INIT_FILE = "mel_descriptor.mem",
    parameter DCT_INIT_FILE = "dct_cosine.mem",
    parameter SCALE_INIT_FILE = "dct_scale.mem",
    parameter logic [23:0] FFT_CONFIG_DATA = 24'h000001
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        s_valid,
    output logic        s_ready,
    input  logic [31:0] s_data,
    input  logic [8:0]  s_index,
    input  logic [31:0] s_frame_id,
    input  logic [31:0] s_start_sample,
    input  logic        s_last,
    output logic        m_valid,
    input  logic        m_ready,
    output logic [31:0] m_data,
    output logic [3:0]  m_coeff_index,
    output logic [31:0] m_frame_id,
    output logic [31:0] m_start_sample,
    output logic        m_last,
    output logic        o_busy,
    output logic [7:0]  o_error,
    output logic        o_frame_done,
    output logic        dbg_valid,
    output logic [3:0]  dbg_stage,
    output logic [15:0] dbg_index,
    output logic [31:0] dbg_data,
    output logic [31:0] dbg_aux
);
    typedef enum logic [5:0] {
        S_CONFIG, S_FEED, S_FFT_READ,
        S_RE_REQ, S_RE_WAIT, S_IM_REQ, S_IM_WAIT,
        S_POWER_ADD_REQ, S_POWER_ADD_WAIT, S_POWER_SCALE_REQ, S_POWER_SCALE_WAIT, S_POWER_WRITE,
        S_MEL_DESC_REQ, S_MEL_DESC_WAIT1, S_MEL_DESC_WAIT2,
        S_MEL_RD_REQ, S_MEL_RD_WAIT1, S_MEL_RD_WAIT2,
        S_MEL_MUL_REQ, S_MEL_MUL_WAIT, S_MEL_ADD_REQ, S_MEL_ADD_WAIT, S_MEL_ADVANCE,
        S_LOG_REQ, S_LOG_WAIT, S_LOG_WRITE,
        S_DCT_RD_REQ, S_DCT_RD_WAIT1, S_DCT_RD_WAIT2,
        S_DCT_MUL_REQ, S_DCT_MUL_WAIT, S_DCT_ADD_REQ, S_DCT_ADD_WAIT, S_DCT_ADVANCE,
        S_SCALE_RD_REQ, S_SCALE_RD_WAIT1, S_SCALE_RD_WAIT2, S_SCALE_REQ, S_SCALE_WAIT,
        S_OUTPUT, S_FAULT
    } state_t;
    localparam logic [31:0] LOG_FLOOR = 32'h2b8cbccc;
    localparam logic [31:0] POWER_SCALE = 32'h3b000000;
    localparam logic [1:0] OP_MUL = 2'b00, OP_ADD = 2'b01, OP_LOG = 2'b11;

    state_t c_state, n_state;
    logic [31:0] frame_reg, frame_next, start_reg, start_next;
    logic [8:0] input_index_reg, input_index_next, fft_index_reg, fft_index_next;
    logic [8:0] bin_reg, bin_next;
    logic [4:0] mel_reg, mel_next, dct_inner_reg, dct_inner_next;
    logic [3:0] coefficient_reg, coefficient_next;
    logic [8:0] mel_address_reg, mel_address_next, mel_last_bin_reg, mel_last_bin_next;
    logic [8:0] dct_address_reg, dct_address_next;
    logic [31:0] real_reg, real_next, imag_reg, imag_next;
    logic [31:0] temporary_reg, temporary_next, product_reg, product_next;
    logic [31:0] accumulator_reg, accumulator_next, log_reg, log_next;
    logic [31:0] output_reg, output_next;
    logic output_valid_reg, output_valid_next, busy_reg, busy_next, done_reg, done_next;
    logic [7:0] error_reg, error_next;
    logic debug_valid_reg, debug_valid_next;
    logic [3:0] debug_stage_reg, debug_stage_next;
    logic [15:0] debug_index_reg, debug_index_next;
    logic [31:0] debug_data_reg, debug_data_next, debug_aux_reg, debug_aux_next;
    logic reset_recovery_reg, reset_recovery_next;
    logic fft_aclken;

    logic fft_config_valid, fft_config_ready;
    logic fft_input_valid, fft_input_ready, fft_input_last;
    logic fft_output_valid, fft_output_ready, fft_output_last;
    logic [63:0] fft_output_data;
    logic [15:0] fft_output_user;
    logic fft_event_last_unexpected, fft_event_last_missing;
    logic alu_req_valid, alu_req_ready, alu_rsp_valid, alu_rsp_ready, alu_error;
    logic [1:0] alu_req_op;
    logic [31:0] alu_req_a, alu_req_b, alu_rsp_data;
    logic power_enable, power_write;
    logic [8:0] power_address;
    logic [31:0] power_read_data;
    logic log_enable, log_write;
    logic [4:0] log_address;
    logic [31:0] log_read_data;
    logic mel_rom_enable, mel_descriptor_enable, dct_rom_enable, scale_rom_enable;
    logic [31:0] mel_read_data, mel_descriptor_data, dct_read_data, scale_read_data;
    logic [9:0] mel_descriptor_limit;

    assign m_valid = output_valid_reg;
    assign m_data = output_reg;
    assign m_coeff_index = coefficient_reg;
    assign m_frame_id = frame_reg;
    assign m_start_sample = start_reg;
    assign m_last = coefficient_reg == 4'd12;
    assign o_busy = busy_reg;
    assign o_error = error_reg;
    assign o_frame_done = done_reg;
    assign dbg_valid = debug_valid_reg;
    assign dbg_stage = debug_stage_reg;
    assign dbg_index = debug_index_reg;
    assign dbg_data = debug_data_reg;
    assign dbg_aux = debug_aux_reg;
    // Pause the vendor FFT while its accepted bin is processed by the ALU.
    // A paused output is not accepted because fft_output_ready is also low.
    assign fft_aclken = !rst_n || reset_recovery_reg ||
        c_state == S_CONFIG || c_state == S_FEED || c_state == S_FFT_READ;
    // Descriptor fields: first bin, inclusive last bin, compact word offset.
    // Extend before arithmetic so the exclusive word limit cannot wrap at 512.
    assign mel_descriptor_limit = {1'b0, mel_descriptor_data[26:18]} +
        {1'b0, mel_descriptor_data[17:9]} - {1'b0, mel_descriptor_data[8:0]} + 10'd1;

    fp32_fft512 U_FFT (
        .aclk(clk), .aresetn(rst_n), .aclken(fft_aclken),
        .s_axis_config_tdata(FFT_CONFIG_DATA), .s_axis_config_tvalid(fft_config_valid),
        .s_axis_config_tready(fft_config_ready),
        .s_axis_data_tdata({32'b0, s_data}), .s_axis_data_tvalid(fft_input_valid),
        .s_axis_data_tready(fft_input_ready), .s_axis_data_tlast(fft_input_last),
        .m_axis_data_tdata(fft_output_data), .m_axis_data_tuser(fft_output_user),
        .m_axis_data_tvalid(fft_output_valid), .m_axis_data_tready(fft_output_ready),
        .m_axis_data_tlast(fft_output_last),
        .event_frame_started(), // Observability only; accepted sample counter defines this wrapper's frame.
        .event_tlast_unexpected(fft_event_last_unexpected),
        .event_tlast_missing(fft_event_last_missing),
        .event_status_channel_halt(), // This generated floating interface has no status stream.
        .event_data_in_channel_halt(), // Input gaps are permitted in nonrealtime mode.
        .event_data_out_channel_halt() // Intentional power-processing backpressure.
    );
    fp32_alu #(.INCLUDE_LOG(1'b1)) U_ALU (
        .clk(clk), .rst_n(rst_n), .req_valid(alu_req_valid), .req_ready(alu_req_ready),
        .req_op(alu_req_op), .req_a(alu_req_a), .req_b(alu_req_b),
        .rsp_valid(alu_rsp_valid), .rsp_ready(alu_rsp_ready), .rsp_data(alu_rsp_data), .o_error(alu_error)
    );
    fp32_storage #(.ADDRESS_WIDTH(9), .WORDS(512), .INIT_ENABLE(0), .INIT_FILE("none")) U_POWER_RAM (
        .clk(clk), .rst_n(rst_n), .enable(power_enable), .write_enable(power_write),
        .address(power_address), .write_data(product_reg), .read_data(power_read_data)
    );
    fp32_storage #(.ADDRESS_WIDTH(5), .WORDS(32), .INIT_ENABLE(0), .INIT_FILE("none")) U_LOG_RAM (
        .clk(clk), .rst_n(rst_n), .enable(log_enable), .write_enable(log_write),
        .address(log_address), .write_data(log_reg), .read_data(log_read_data)
    );
    fp32_storage #(.ADDRESS_WIDTH(9), .WORDS(512), .INIT_ENABLE(1), .INIT_FILE(MEL_INIT_FILE)) U_MEL_ROM (
        .clk(clk), .rst_n(rst_n), .enable(mel_rom_enable), .write_enable(1'b0),
        .address(mel_address_reg), .write_data(32'b0), .read_data(mel_read_data)
    );
    fp32_storage #(.ADDRESS_WIDTH(5), .WORDS(32), .INIT_ENABLE(1), .INIT_FILE(MEL_DESCRIPTOR_INIT_FILE)) U_MEL_DESCRIPTOR_ROM (
        .clk(clk), .rst_n(rst_n), .enable(mel_descriptor_enable), .write_enable(1'b0),
        .address(mel_reg), .write_data(32'b0), .read_data(mel_descriptor_data)
    );
    fp32_storage #(.ADDRESS_WIDTH(9), .WORDS(512), .INIT_ENABLE(1), .INIT_FILE(DCT_INIT_FILE)) U_DCT_ROM (
        .clk(clk), .rst_n(rst_n), .enable(dct_rom_enable), .write_enable(1'b0),
        .address(dct_address_reg), .write_data(32'b0), .read_data(dct_read_data)
    );
    fp32_storage #(.ADDRESS_WIDTH(4), .WORDS(16), .INIT_ENABLE(1), .INIT_FILE(SCALE_INIT_FILE)) U_SCALE_ROM (
        .clk(clk), .rst_n(rst_n), .enable(scale_rom_enable), .write_enable(1'b0),
        .address(coefficient_reg), .write_data(32'b0), .read_data(scale_read_data)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_CONFIG;
            frame_reg <= 32'b0;
            start_reg <= 32'b0;
            input_index_reg <= 9'b0;
            fft_index_reg <= 9'b0;
            bin_reg <= 9'b0;
            mel_reg <= 5'b0;
            dct_inner_reg <= 5'b0;
            coefficient_reg <= 4'b0;
            mel_address_reg <= 9'b0;
            mel_last_bin_reg <= 9'b0;
            dct_address_reg <= 9'b0;
            real_reg <= 32'b0;
            imag_reg <= 32'b0;
            temporary_reg <= 32'b0;
            product_reg <= 32'b0;
            accumulator_reg <= 32'b0;
            log_reg <= 32'b0;
            output_reg <= 32'b0;
            output_valid_reg <= 1'b0;
            busy_reg <= 1'b0;
            done_reg <= 1'b0;
            error_reg <= 8'b0;
            debug_valid_reg <= 1'b0;
            debug_stage_reg <= 4'b0;
            debug_index_reg <= 16'b0;
            debug_data_reg <= 32'b0;
            debug_aux_reg <= 32'b0;
            reset_recovery_reg <= 1'b1;
        end else begin
            c_state <= n_state;
            frame_reg <= frame_next;
            start_reg <= start_next;
            input_index_reg <= input_index_next;
            fft_index_reg <= fft_index_next;
            bin_reg <= bin_next;
            mel_reg <= mel_next;
            dct_inner_reg <= dct_inner_next;
            coefficient_reg <= coefficient_next;
            mel_address_reg <= mel_address_next;
            mel_last_bin_reg <= mel_last_bin_next;
            dct_address_reg <= dct_address_next;
            real_reg <= real_next;
            imag_reg <= imag_next;
            temporary_reg <= temporary_next;
            product_reg <= product_next;
            accumulator_reg <= accumulator_next;
            log_reg <= log_next;
            output_reg <= output_next;
            output_valid_reg <= output_valid_next;
            busy_reg <= busy_next;
            done_reg <= done_next;
            error_reg <= error_next;
            debug_valid_reg <= debug_valid_next;
            debug_stage_reg <= debug_stage_next;
            debug_index_reg <= debug_index_next;
            debug_data_reg <= debug_data_next;
            debug_aux_reg <= debug_aux_next;
            reset_recovery_reg <= reset_recovery_next;
        end
    end

    always_comb begin
        n_state = c_state;
        frame_next = frame_reg;
        start_next = start_reg;
        input_index_next = input_index_reg;
        fft_index_next = fft_index_reg;
        bin_next = bin_reg;
        mel_next = mel_reg;
        dct_inner_next = dct_inner_reg;
        coefficient_next = coefficient_reg;
        mel_address_next = mel_address_reg;
        mel_last_bin_next = mel_last_bin_reg;
        dct_address_next = dct_address_reg;
        real_next = real_reg;
        imag_next = imag_reg;
        temporary_next = temporary_reg;
        product_next = product_reg;
        accumulator_next = accumulator_reg;
        log_next = log_reg;
        output_next = output_reg;
        output_valid_next = output_valid_reg;
        busy_next = busy_reg;
        done_next = 1'b0;
        error_next = error_reg;
        debug_valid_next = 1'b0;
        debug_stage_next = debug_stage_reg;
        debug_index_next = debug_index_reg;
        debug_data_next = debug_data_reg;
        debug_aux_next = debug_aux_reg;
        reset_recovery_next = 1'b0;
        s_ready = 1'b0;
        fft_config_valid = 1'b0;
        fft_input_valid = 1'b0;
        fft_input_last = 1'b0;
        fft_output_ready = 1'b0;
        alu_req_valid = 1'b0;
        alu_req_op = OP_MUL;
        alu_req_a = 32'b0;
        alu_req_b = 32'b0;
        alu_rsp_ready = 1'b0;
        power_enable = 1'b0;
        power_write = 1'b0;
        power_address = bin_reg;
        log_enable = 1'b0;
        log_write = 1'b0;
        log_address = dct_inner_reg;
        mel_rom_enable = 1'b0;
        mel_descriptor_enable = 1'b0;
        dct_rom_enable = 1'b0;
        scale_rom_enable = 1'b0;
        case (c_state)
            S_CONFIG: begin
                fft_config_valid = rst_n && !reset_recovery_reg;
                if (fft_config_valid && fft_config_ready) n_state = S_FEED;
            end
            S_FEED: begin
                s_ready = fft_input_ready && rst_n;
                fft_input_valid = s_valid && rst_n;
                fft_input_last = s_last;
                if (s_valid && s_ready) begin
                    busy_next = 1'b1;
                    if (s_index != input_index_reg || s_last != (input_index_reg == 9'd511)) begin
                        error_next = 8'd1;
                        n_state = S_FAULT;
                    end else if (s_data[30:23] == 8'hff) begin
                        error_next = 8'd3;
                        n_state = S_FAULT;
                    end else if (input_index_reg != 9'd0
                                 && (s_frame_id != frame_reg || s_start_sample != start_reg)) begin
                        error_next = 8'd2;
                        n_state = S_FAULT;
                    end else begin
                        if (input_index_reg == 9'd0) begin
                            frame_next = s_frame_id;
                            start_next = s_start_sample;
                        end
                        if (input_index_reg == 9'd511) begin
                            input_index_next = 9'd0;
                            fft_index_next = 9'd0;
                            n_state = S_FFT_READ;
                        end else begin
                            input_index_next = input_index_reg + 9'd1;
                        end
                    end
                end
            end
            S_FFT_READ: begin
                fft_output_ready = 1'b1;
                if (fft_output_valid) begin
                    if (fft_output_user[8:0] != fft_index_reg
                        || fft_output_last != (fft_index_reg == 9'd511)) begin
                        error_next = 8'd4;
                        n_state = S_FAULT;
                    end else if (fft_output_data[30:23] == 8'hff || fft_output_data[62:55] == 8'hff) begin
                        error_next = 8'd5;
                        n_state = S_FAULT;
                    end else if (fft_index_reg <= 9'd256) begin
                        real_next = fft_output_data[31:0];
                        imag_next = fft_output_data[63:32];
                        debug_valid_next = 1'b1;
                        debug_stage_next = 4'd1;
                        debug_index_next = {7'b0, fft_index_reg};
                        debug_data_next = fft_output_data[31:0];
                        debug_aux_next = fft_output_data[63:32];
                        n_state = S_RE_REQ;
                    end else if (fft_index_reg == 9'd511) begin
                        bin_next = 9'd0;
                        mel_next = 5'd0;
                        mel_address_next = 9'd0;
                        accumulator_next = 32'b0;
                        n_state = S_MEL_DESC_REQ;
                    end else begin
                        fft_index_next = fft_index_reg + 9'd1;
                    end
                end
            end
            S_RE_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_a = real_reg;
                alu_req_b = real_reg;
                if (alu_req_ready) n_state = S_RE_WAIT;
            end
            S_RE_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    temporary_next = alu_rsp_data;
                    n_state = S_IM_REQ;
                end
            end
            S_IM_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_a = imag_reg;
                alu_req_b = imag_reg;
                if (alu_req_ready) n_state = S_IM_WAIT;
            end
            S_IM_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    product_next = alu_rsp_data;
                    n_state = S_POWER_ADD_REQ;
                end
            end
            S_POWER_ADD_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_op = OP_ADD;
                alu_req_a = temporary_reg;
                alu_req_b = product_reg;
                if (alu_req_ready) n_state = S_POWER_ADD_WAIT;
            end
            S_POWER_ADD_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    temporary_next = alu_rsp_data;
                    n_state = S_POWER_SCALE_REQ;
                end
            end
            S_POWER_SCALE_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_a = temporary_reg;
                alu_req_b = POWER_SCALE;
                if (alu_req_ready) n_state = S_POWER_SCALE_WAIT;
            end
            S_POWER_SCALE_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    if (alu_rsp_data[31] && alu_rsp_data[30:0] != 31'b0) begin
                        error_next = 8'd8;
                        n_state = S_FAULT;
                    end else begin
                        product_next = alu_rsp_data;
                        n_state = S_POWER_WRITE;
                    end
                end
            end
            S_POWER_WRITE: begin
                power_enable = 1'b1;
                power_write = 1'b1;
                power_address = fft_index_reg;
                debug_valid_next = 1'b1;
                debug_stage_next = 4'd2;
                debug_index_next = {7'b0, fft_index_reg};
                debug_data_next = product_reg;
                debug_aux_next = 32'b0;
                fft_index_next = fft_index_reg + 9'd1;
                n_state = S_FFT_READ;
            end
            S_MEL_DESC_REQ: begin
                mel_descriptor_enable = 1'b1;
                n_state = S_MEL_DESC_WAIT1;
            end
            S_MEL_DESC_WAIT1: n_state = S_MEL_DESC_WAIT2;
            S_MEL_DESC_WAIT2: begin
                if (mel_descriptor_data[31:27] != 5'b0 ||
                    mel_descriptor_data[8:0] > mel_descriptor_data[17:9] ||
                    mel_descriptor_data[17:9] > 9'd256 || mel_descriptor_limit > 10'd459) begin
                    error_next = 8'd11;
                    n_state = S_FAULT;
                end else begin
                    bin_next = mel_descriptor_data[8:0];
                    mel_last_bin_next = mel_descriptor_data[17:9];
                    mel_address_next = mel_descriptor_data[26:18];
                    n_state = S_MEL_RD_REQ;
                end
            end
            S_MEL_RD_REQ: begin
                power_enable = 1'b1;
                mel_rom_enable = 1'b1;
                n_state = S_MEL_RD_WAIT1;
            end
            S_MEL_RD_WAIT1: n_state = S_MEL_RD_WAIT2;
            // The generator proves each compact row is exactly the original
            // ascending nonzero subsequence. No arithmetic operation is removed.
            S_MEL_RD_WAIT2: n_state = S_MEL_MUL_REQ;
            S_MEL_MUL_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_a = power_read_data;
                alu_req_b = mel_read_data;
                if (alu_req_ready) n_state = S_MEL_MUL_WAIT;
            end
            S_MEL_MUL_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    product_next = alu_rsp_data;
                    n_state = S_MEL_ADD_REQ;
                end
            end
            S_MEL_ADD_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_op = OP_ADD;
                alu_req_a = accumulator_reg;
                alu_req_b = product_reg;
                if (alu_req_ready) n_state = S_MEL_ADD_WAIT;
            end
            S_MEL_ADD_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    accumulator_next = alu_rsp_data;
                    n_state = S_MEL_ADVANCE;
                end
            end
            S_MEL_ADVANCE: begin
                if (bin_reg == mel_last_bin_reg) begin
                    if (accumulator_reg[31] && accumulator_reg[30:0] != 31'b0) begin
                        error_next = 8'd8;
                        n_state = S_FAULT;
                    end else begin
                        debug_valid_next = 1'b1;
                        debug_stage_next = 4'd3;
                        debug_index_next = {11'b0, mel_reg};
                        debug_data_next = accumulator_reg;
                        debug_aux_next = 32'b0;
                        n_state = S_LOG_REQ;
                    end
                end else begin
                    bin_next = bin_reg + 9'd1;
                    mel_address_next = mel_address_reg + 9'd1;
                    n_state = S_MEL_RD_REQ;
                end
            end
            S_LOG_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_op = OP_LOG;
                // IEEE754 positive finite bit order matches numeric order; -0 is floored too.
                alu_req_a = (accumulator_reg[31] || accumulator_reg < LOG_FLOOR)
                            ? LOG_FLOOR : accumulator_reg;
                if (alu_req_ready) n_state = S_LOG_WAIT;
            end
            S_LOG_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    log_next = alu_rsp_data;
                    debug_valid_next = 1'b1;
                    debug_stage_next = 4'd4;
                    debug_index_next = {11'b0, mel_reg};
                    debug_data_next = alu_rsp_data;
                    debug_aux_next = 32'b0;
                    n_state = S_LOG_WRITE;
                end
            end
            S_LOG_WRITE: begin
                log_enable = 1'b1;
                log_write = 1'b1;
                log_address = mel_reg;
                accumulator_next = 32'b0;
                if (mel_reg == 5'd25) begin
                    dct_inner_next = 5'd0;
                    coefficient_next = 4'd0;
                    dct_address_next = 9'd0;
                    n_state = S_DCT_RD_REQ;
                end else begin
                    mel_next = mel_reg + 5'd1;
                    n_state = S_MEL_DESC_REQ;
                end
            end
            S_DCT_RD_REQ: begin
                log_enable = 1'b1;
                dct_rom_enable = 1'b1;
                n_state = S_DCT_RD_WAIT1;
            end
            S_DCT_RD_WAIT1: n_state = S_DCT_RD_WAIT2;
            S_DCT_RD_WAIT2: n_state = S_DCT_MUL_REQ;
            S_DCT_MUL_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_a = log_read_data;
                alu_req_b = dct_read_data;
                if (alu_req_ready) n_state = S_DCT_MUL_WAIT;
            end
            S_DCT_MUL_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    product_next = alu_rsp_data;
                    n_state = S_DCT_ADD_REQ;
                end
            end
            S_DCT_ADD_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_op = OP_ADD;
                alu_req_a = accumulator_reg;
                alu_req_b = product_reg;
                if (alu_req_ready) n_state = S_DCT_ADD_WAIT;
            end
            S_DCT_ADD_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    accumulator_next = alu_rsp_data;
                    n_state = S_DCT_ADVANCE;
                end
            end
            S_DCT_ADVANCE: begin
                if (dct_inner_reg == 5'd25) begin
                    n_state = S_SCALE_RD_REQ;
                end else begin
                    dct_inner_next = dct_inner_reg + 5'd1;
                    dct_address_next = dct_address_reg + 9'd1;
                    n_state = S_DCT_RD_REQ;
                end
            end
            S_SCALE_RD_REQ: begin
                scale_rom_enable = 1'b1;
                n_state = S_SCALE_RD_WAIT1;
            end
            S_SCALE_RD_WAIT1: n_state = S_SCALE_RD_WAIT2;
            S_SCALE_RD_WAIT2: n_state = S_SCALE_REQ;
            S_SCALE_REQ: begin
                alu_req_valid = 1'b1;
                alu_req_a = accumulator_reg;
                alu_req_b = scale_read_data;
                if (alu_req_ready) n_state = S_SCALE_WAIT;
            end
            S_SCALE_WAIT: begin
                alu_rsp_ready = 1'b1;
                if (alu_rsp_valid) begin
                    output_next = alu_rsp_data;
                    output_valid_next = 1'b1;
                    debug_valid_next = 1'b1;
                    debug_stage_next = 4'd5;
                    debug_index_next = {12'b0, coefficient_reg};
                    debug_data_next = alu_rsp_data;
                    debug_aux_next = 32'b0;
                    n_state = S_OUTPUT;
                end
            end
            S_OUTPUT: begin
                if (output_valid_reg && m_ready) begin
                    output_valid_next = 1'b0;
                    if (coefficient_reg == 4'd12) begin
                        busy_next = 1'b0;
                        done_next = 1'b1;
                        n_state = S_CONFIG;
                    end else begin
                        coefficient_next = coefficient_reg + 4'd1;
                        dct_inner_next = 5'd0;
                        dct_address_next = dct_address_reg + 9'd1;
                        accumulator_next = 32'b0;
                        n_state = S_DCT_RD_REQ;
                    end
                end
            end
            S_FAULT: begin
                output_valid_next = 1'b0;
                busy_next = 1'b1;
            end
            default: begin
                error_next = 8'd10;
                output_valid_next = 1'b0;
                n_state = S_FAULT;
            end
        endcase
        // These faults override normal transitions. Fault recovery requires reset.
        if (alu_rsp_valid && alu_rsp_ready && alu_rsp_data[30:23] == 8'hff) begin
            error_next = 8'd9;
            debug_valid_next = 1'b0;
            output_valid_next = 1'b0;
            n_state = S_FAULT;
        end
        if (alu_error) begin
            error_next = 8'd7;
            output_valid_next = 1'b0;
            n_state = S_FAULT;
        end
        if (fft_event_last_unexpected || fft_event_last_missing) begin
            error_next = 8'd6;
            output_valid_next = 1'b0;
            n_state = S_FAULT;
        end
    end
endmodule

// Vendor XPM owns the RAM/ROM implementation and optional file initialization.
// New user RTL contains no array reset, behavioral initialization, or memory loop.
module fp32_storage #(
    parameter integer ADDRESS_WIDTH = 9,
    parameter integer WORDS = 512,
    parameter integer INIT_ENABLE = 0,
    parameter INIT_FILE = "none"
) (
    input logic clk,
    input logic rst_n,
    input logic enable,
    input logic write_enable,
    input logic [ADDRESS_WIDTH-1:0] address,
    input logic [31:0] write_data,
    output logic [31:0] read_data
);
    xpm_memory_spram #(
        .ADDR_WIDTH_A(ADDRESS_WIDTH), .AUTO_SLEEP_TIME(0), .BYTE_WRITE_WIDTH_A(32),
        .CASCADE_HEIGHT(0), .ECC_MODE("no_ecc"), .MEMORY_INIT_FILE(INIT_FILE),
        .MEMORY_INIT_PARAM("0"), .MEMORY_OPTIMIZATION("true"), .MEMORY_PRIMITIVE("block"),
        .MEMORY_SIZE(WORDS * 32), .MESSAGE_CONTROL(0), .READ_DATA_WIDTH_A(32),
        .READ_LATENCY_A(2), .READ_RESET_VALUE_A("0"), .RST_MODE_A("SYNC"),
        .SIM_ASSERT_CHK(1), .USE_MEM_INIT(INIT_ENABLE), .WAKEUP_TIME("disable_sleep"),
        .WRITE_DATA_WIDTH_A(32), .WRITE_MODE_A("read_first")
    ) U_XPM (
        .clka(clk), .rsta(!rst_n), .ena(enable && rst_n), .regcea(1'b1),
        .wea(write_enable), .addra(address), .dina(write_data), .douta(read_data),
        .sleep(1'b0), .injectsbiterra(1'b0), .injectdbiterra(1'b0),
        .sbiterra(), .dbiterra() // ECC disabled; no parity/error bits exist in these memories.
    );
endmodule
