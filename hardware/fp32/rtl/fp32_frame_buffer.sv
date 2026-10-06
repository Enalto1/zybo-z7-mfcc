module fp32_frame_buffer #(
    parameter int DATA_WIDTH = 32
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  clip_start_valid,
    output logic                  clip_start_ready,
    input  logic                  s_valid,
    output logic                  s_ready,
    input  logic [DATA_WIDTH-1:0] s_data,
    input  logic                  clip_end_valid,
    output logic                  clip_end_ready,
    output logic                  m_valid,
    input  logic                  m_ready,
    output logic [DATA_WIDTH-1:0] m_data,
    output logic [8:0]            m_sample_index,
    output logic [31:0]           m_frame_id,
    output logic [31:0]           m_start_sample,
    output logic                  m_last,
    output logic                  clip_done_valid,
    input  logic                  clip_done_ready,
    output logic                  error
);
    typedef enum logic [2:0] {
        S_IDLE, S_COLLECT, S_READ, S_WAIT, S_CAPTURE, S_SEND, S_DONE
    } state_t;
    localparam int MEMORY_BITS = 512 * DATA_WIDTH;

    state_t c_state, n_state;
    logic [8:0] write_addr_reg, write_addr_next;
    logic [8:0] read_addr_reg, read_addr_next;
    logic [9:0] needed_reg, needed_next;
    logic [8:0] sample_index_reg, sample_index_next;
    logic [31:0] frame_id_reg, frame_id_next;
    logic [31:0] start_sample_reg, start_sample_next;
    logic [DATA_WIDTH-1:0] data_reg, data_next;
    logic valid_reg, valid_next;
    logic eof_reg, eof_next;
    logic done_reg, done_next;
    logic error_reg, error_next;

    logic write_fire;
    logic end_fire;
    logic read_enable;
    logic [DATA_WIDTH-1:0] ram_data;

    assign clip_start_ready = rst_n && (c_state == S_IDLE);
    assign s_ready = rst_n && (c_state == S_COLLECT) && !eof_reg;
    assign write_fire = s_valid && s_ready;
    assign clip_end_ready = rst_n && !eof_reg && !write_fire &&
        ((c_state == S_COLLECT) || (c_state == S_READ) ||
         (c_state == S_WAIT) || (c_state == S_CAPTURE) || (c_state == S_SEND));
    assign end_fire = clip_end_valid && clip_end_ready;
    assign read_enable = rst_n && (c_state == S_READ);
    assign m_valid = valid_reg && rst_n;
    assign m_data = data_reg;
    assign m_sample_index = sample_index_reg;
    assign m_frame_id = frame_id_reg;
    assign m_start_sample = start_sample_reg;
    assign m_last = valid_reg && (sample_index_reg == 9'd511) && rst_n;
    assign clip_done_valid = done_reg && rst_n;
    assign error = error_reg;

    generate
        if (DATA_WIDTH != 32) begin : G_UNSUPPORTED_WIDTH
            // F0 verifies the 32-bit transport contract only. Fail elaboration.
            fp32_frame_buffer_requires_DATA_WIDTH_32 U_INVALID_WIDTH ();
        end
    endgenerate

    // Vendor macro is exempt from authored-RTL syntax restrictions. No RAM reset.
    xpm_memory_sdpram #(
        .ADDR_WIDTH_A(9),
        .ADDR_WIDTH_B(9),
        .AUTO_SLEEP_TIME(0),
        .BYTE_WRITE_WIDTH_A(DATA_WIDTH),
        .CASCADE_HEIGHT(0),
        .CLOCKING_MODE("common_clock"),
        .ECC_MODE("no_ecc"),
        .MEMORY_INIT_FILE("none"),
        .MEMORY_INIT_PARAM("0"),
        .MEMORY_OPTIMIZATION("true"),
        .MEMORY_PRIMITIVE("block"),
        .MEMORY_SIZE(MEMORY_BITS),
        .MESSAGE_CONTROL(1),
        .READ_DATA_WIDTH_B(DATA_WIDTH),
        .READ_LATENCY_B(2),
        .READ_RESET_VALUE_B("0"),
        .RST_MODE_A("SYNC"),
        .RST_MODE_B("SYNC"),
        .SIM_ASSERT_CHK(1),
        .USE_EMBEDDED_CONSTRAINT(0),
        .USE_MEM_INIT(0),
        .WAKEUP_TIME("disable_sleep"),
        .WRITE_DATA_WIDTH_A(DATA_WIDTH),
        .WRITE_MODE_B("no_change")
    ) U_HISTORY (
        .dbiterrb(), // ECC is disabled.
        .doutb(ram_data),
        .sbiterrb(), // ECC is disabled.
        .addra(write_addr_reg),
        .addrb(read_addr_reg),
        .clka(clk),
        .clkb(clk),
        .dina(s_data),
        .ena(write_fire),
        .enb(read_enable),
        .injectdbiterra(1'b0),
        .injectsbiterra(1'b0),
        .regceb(1'b1),
        .rstb(1'b0),
        .sleep(1'b0),
        .wea(write_fire)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE;
            write_addr_reg <= 9'd0;
            read_addr_reg <= 9'd0;
            needed_reg <= 10'd512;
            sample_index_reg <= 9'd0;
            frame_id_reg <= 32'd0;
            start_sample_reg <= 32'd0;
            data_reg <= {DATA_WIDTH{1'b0}};
            valid_reg <= 1'b0;
            eof_reg <= 1'b0;
            done_reg <= 1'b0;
            error_reg <= 1'b0;
        end else begin
            c_state <= n_state;
            write_addr_reg <= write_addr_next;
            read_addr_reg <= read_addr_next;
            needed_reg <= needed_next;
            sample_index_reg <= sample_index_next;
            frame_id_reg <= frame_id_next;
            start_sample_reg <= start_sample_next;
            data_reg <= data_next;
            valid_reg <= valid_next;
            eof_reg <= eof_next;
            done_reg <= done_next;
            error_reg <= error_next;
        end
    end

    always_comb begin
        n_state = c_state;
        write_addr_next = write_addr_reg;
        read_addr_next = read_addr_reg;
        needed_next = needed_reg;
        sample_index_next = sample_index_reg;
        frame_id_next = frame_id_reg;
        start_sample_next = start_sample_reg;
        data_next = data_reg;
        valid_next = valid_reg;
        eof_next = eof_reg;
        done_next = done_reg;
        error_next = error_reg;

        case (c_state)
            S_IDLE: begin
                valid_next = 1'b0;
                done_next = 1'b0;
                if (clip_start_valid && clip_start_ready) begin
                    write_addr_next = 9'd0;
                    read_addr_next = 9'd0;
                    needed_next = 10'd512;
                    sample_index_next = 9'd0;
                    frame_id_next = 32'd0;
                    start_sample_next = 32'd0;
                    eof_next = 1'b0;
                    error_next = 1'b0;
                    n_state = S_COLLECT;
                end
            end
            S_COLLECT: begin
                if (write_fire) begin
                    write_addr_next = write_addr_reg + 9'd1;
                    if (needed_reg == 10'd1) begin
                        // The next overwritten word is oldest after this write.
                        read_addr_next = write_addr_reg + 9'd1;
                        sample_index_next = 9'd0;
                        needed_next = 10'd160;
                        n_state = S_READ;
                    end else begin
                        needed_next = needed_reg - 10'd1;
                    end
                end else if (end_fire || eof_reg) begin
                    done_next = 1'b1;
                    n_state = S_DONE;
                end
            end
            S_READ: begin
                // Exactly one read request; all input writes remain inhibited.
                n_state = S_WAIT;
            end
            S_WAIT: begin
                n_state = S_CAPTURE;
            end
            S_CAPTURE: begin
                data_next = ram_data;
                valid_next = 1'b1;
                n_state = S_SEND;
            end
            S_SEND: begin
                if (valid_reg && m_ready) begin
                    valid_next = 1'b0;
                    if (sample_index_reg == 9'd511) begin
                        if (eof_reg || end_fire) begin
                            done_next = 1'b1;
                            n_state = S_DONE;
                        end else if ((frame_id_reg == 32'hffffffff) ||
                                     (start_sample_reg > 32'hffffff5f)) begin
                            error_next = 1'b1;
                            done_next = 1'b1;
                            n_state = S_DONE;
                        end else begin
                            frame_id_next = frame_id_reg + 32'd1;
                            start_sample_next = start_sample_reg + 32'd160;
                            n_state = S_COLLECT;
                        end
                    end else begin
                        sample_index_next = sample_index_reg + 9'd1;
                        read_addr_next = read_addr_reg + 9'd1;
                        n_state = S_READ;
                    end
                end
            end
            S_DONE: begin
                valid_next = 1'b0;
                if (done_reg && clip_done_ready) begin
                    done_next = 1'b0;
                    n_state = S_IDLE;
                end
            end
            default: begin
                valid_next = 1'b0;
                done_next = 1'b1;
                error_next = 1'b1;
                n_state = S_DONE;
            end
        endcase

        // EOF may arrive during a pending RAM read or a stalled output beat.
        if (end_fire) begin
            eof_next = 1'b1;
        end
    end
endmodule
