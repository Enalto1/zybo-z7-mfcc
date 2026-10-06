module fixed_spectral_tail #(
    parameter MEL_INIT_FILE = "mel_sparse_fw16.mem"
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               i_frame_start,
    output logic               o_frame_ready,
    input  logic [31:0]        i_frame_id,
    input  logic signed [7:0]  i_bfp_s,
    input  logic               i_fft_valid,
    input  logic signed [19:0] i_fft_re,
    input  logic signed [19:0] i_fft_im,
    input  logic [8:0]         i_fft_bin,
    input  logic               i_fft_last,
    input  logic               i_fft_overflow,
    output logic               o_valid,
    input  logic               i_ready,
    output logic [59:0]        o_mel,
    output logic [4:0]         o_band,
    output logic [31:0]        o_frame_id,
    output logic signed [7:0]  o_bfp_s,
    output logic               o_last,
    output logic               o_overflow,
    output logic               o_protocol_error,
    output logic               o_power_valid,
    output logic [39:0]        o_power,
    output logic [8:0]         o_power_bin
);
    typedef enum logic [2:0] {S_IDLE, S_CAPTURE, S_READ, S_DRAIN,
                              S_OUTPUT, S_DROP, S_DESC} state_t;
    state_t c_state, n_state;
    logic [31:0] frame_reg, frame_next;
    logic signed [7:0] bfp_reg, bfp_next;
    logic [8:0] count_reg, count_next, bin_reg, bin_next;
    logic [4:0] band_reg, band_next;
    logic [8:0] coeff_addr_reg, coeff_addr_next;
    logic [8:0] last_bin_reg, last_bin_next;
    logic [8:0] descriptor_start, descriptor_length, descriptor_offset;
    logic [56:0] product_reg, product_next;
    logic read_valid_reg, read_valid_next, read_last_reg, read_last_next;
    logic product_valid_reg, product_valid_next, product_last_reg, product_last_next;
    logic [59:0] acc_reg, acc_next;
    logic overflow_reg, overflow_next, error_reg, error_next;
    logic power_valid_reg, power_valid_next;
    logic [39:0] power_reg, power_next;
    logic [8:0] power_bin_reg, power_bin_next;
    logic ram_en, ram_we, rom_en;
    logic [8:0] ram_addr;
    logic [39:0] ram_data;
    logic [16:0] coeff_data;
    logic signed [19:0] fft_re_reg, fft_re_next, fft_im_reg, fft_im_next;
    logic fft_valid_reg, fft_valid_next, fft_last_reg, fft_last_next;
    logic fft_overflow_reg, fft_overflow_next;
    logic [8:0] fft_bin_reg, fft_bin_next;
    logic [39:0] square_re_reg, square_re_next, square_im_reg, square_im_next;
    logic square_valid_reg, square_valid_next, square_last_reg, square_last_next;
    logic square_overflow_reg, square_overflow_next;
    logic [8:0] square_bin_reg, square_bin_next;
    logic signed [39:0] square_re_product, square_im_product;
    logic [40:0] power_sum;
    logic [56:0] mel_product;
    logic [60:0] mel_sum;

    assign power_sum = {1'b0, square_re_reg} + {1'b0, square_im_reg};
    assign square_re_product = fft_re_reg * fft_re_reg;
    assign square_im_product = fft_im_reg * fft_im_reg;
    assign mel_product = ram_data * coeff_data;
    assign mel_sum = {1'b0, acc_reg} + {4'b0000, product_reg};
    assign o_frame_ready = (c_state == S_IDLE);
    assign o_valid = (c_state == S_OUTPUT);
    assign o_mel = acc_reg;
    assign o_band = band_reg;
    assign o_frame_id = frame_reg;
    assign o_bfp_s = bfp_reg;
    assign o_last = (band_reg == 5'd25);
    assign o_overflow = overflow_reg;
    assign o_protocol_error = error_reg;
    assign o_power_valid = power_valid_reg;
    assign o_power = power_reg;
    assign o_power_bin = power_bin_reg;

    fixed_mel_descriptor U_DESCRIPTOR (
        .i_band(band_reg), .o_start_bin(descriptor_start),
        .o_length(descriptor_length), .o_offset(descriptor_offset)
    );

    // Vendor RAM has no content reset. Validity is entirely in the FSM.
    xpm_memory_spram #(
        .MEMORY_SIZE(20480), .MEMORY_PRIMITIVE("block"),
        .USE_MEM_INIT(0), .WRITE_DATA_WIDTH_A(40), .READ_DATA_WIDTH_A(40),
        .BYTE_WRITE_WIDTH_A(40), .ADDR_WIDTH_A(9), .READ_LATENCY_A(1),
        .WRITE_MODE_A("read_first"), .RST_MODE_A("SYNC")
    ) U_POWER_RAM (
        .sleep(1'b0), .clka(clk), .rsta(1'b0), .ena(ram_en), .regcea(1'b1),
        .wea(ram_we), .addra(ram_addr), .dina(power_sum[39:0]),
        .injectsbiterra(1'b0), .injectdbiterra(1'b0), .douta(ram_data),
        .sbiterra(), .dbiterra() // ECC disabled.
    );
    xpm_memory_sprom #(
        .MEMORY_SIZE(8704), .MEMORY_PRIMITIVE("block"),
        .MEMORY_INIT_FILE(MEL_INIT_FILE), .READ_DATA_WIDTH_A(17),
        .ADDR_WIDTH_A(9), .READ_LATENCY_A(1), .RST_MODE_A("SYNC")
    ) U_MEL_ROM (
        .sleep(1'b0), .clka(clk), .rsta(1'b0), .ena(rom_en), .regcea(1'b1),
        .addra(coeff_addr_reg), .injectsbiterra(1'b0), .injectdbiterra(1'b0),
        .douta(coeff_data), .sbiterra(), .dbiterra() // ECC disabled.
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE;
            frame_reg <= 32'd0;
            bfp_reg <= 8'sd0;
            count_reg <= 9'd0;
            bin_reg <= 9'd0;
            band_reg <= 5'd0;
            coeff_addr_reg <= 9'd0;
            last_bin_reg <= 9'd0;
            product_reg <= 57'd0;
            read_valid_reg <= 1'b0; read_last_reg <= 1'b0;
            product_valid_reg <= 1'b0; product_last_reg <= 1'b0;
            acc_reg <= 60'd0;
            overflow_reg <= 1'b0;
            error_reg <= 1'b0;
            power_valid_reg <= 1'b0;
            power_reg <= 40'd0;
            power_bin_reg <= 9'd0;
            fft_re_reg <= 20'sd0;
            fft_im_reg <= 20'sd0;
            fft_valid_reg <= 1'b0;
            fft_bin_reg <= 9'd0;
            fft_last_reg <= 1'b0;
            fft_overflow_reg <= 1'b0;
            square_re_reg <= 40'd0;
            square_im_reg <= 40'd0;
            square_valid_reg <= 1'b0;
            square_bin_reg <= 9'd0;
            square_last_reg <= 1'b0;
            square_overflow_reg <= 1'b0;
        end else begin
            c_state <= n_state;
            frame_reg <= frame_next;
            bfp_reg <= bfp_next;
            count_reg <= count_next;
            bin_reg <= bin_next;
            band_reg <= band_next;
            coeff_addr_reg <= coeff_addr_next;
            last_bin_reg <= last_bin_next;
            product_reg <= product_next;
            read_valid_reg <= read_valid_next; read_last_reg <= read_last_next;
            product_valid_reg <= product_valid_next; product_last_reg <= product_last_next;
            acc_reg <= acc_next;
            overflow_reg <= overflow_next;
            error_reg <= error_next;
            power_valid_reg <= power_valid_next;
            power_reg <= power_next;
            power_bin_reg <= power_bin_next;
            fft_re_reg <= fft_re_next;
            fft_im_reg <= fft_im_next;
            fft_valid_reg <= fft_valid_next;
            fft_bin_reg <= fft_bin_next;
            fft_last_reg <= fft_last_next;
            fft_overflow_reg <= fft_overflow_next;
            square_re_reg <= square_re_next;
            square_im_reg <= square_im_next;
            square_valid_reg <= square_valid_next;
            square_bin_reg <= square_bin_next;
            square_last_reg <= square_last_next;
            square_overflow_reg <= square_overflow_next;
        end
    end

    always_comb begin
        n_state = c_state;
        frame_next = frame_reg;
        bfp_next = bfp_reg;
        count_next = count_reg;
        bin_next = bin_reg;
        band_next = band_reg;
        coeff_addr_next = coeff_addr_reg;
        last_bin_next = last_bin_reg;
        product_next = read_valid_reg ? mel_product : product_reg;
        read_valid_next = 1'b0; read_last_next = 1'b0;
        product_valid_next = read_valid_reg; product_last_next = read_last_reg;
        acc_next = acc_reg;
        overflow_next = overflow_reg;
        error_next = error_reg;
        power_valid_next = 1'b0;
        power_next = power_reg;
        power_bin_next = power_bin_reg;
        // The core cannot stop. These two stages advance on every clock and
        // preserve each transaction's valid, bin, last and overflow metadata.
        // Registers split FFT BRAM -> multiplier -> add/Power BRAM paths.
        fft_re_next = i_fft_re;
        fft_im_next = i_fft_im;
        fft_valid_next = i_fft_valid;
        fft_bin_next = i_fft_bin;
        fft_last_next = i_fft_last;
        fft_overflow_next = i_fft_overflow;
        square_re_next = $unsigned(square_re_product);
        square_im_next = $unsigned(square_im_product);
        square_valid_next = fft_valid_reg;
        square_bin_next = fft_bin_reg;
        square_last_next = fft_last_reg;
        square_overflow_next = fft_overflow_reg;
        ram_en = 1'b0;
        ram_we = 1'b0;
        ram_addr = bin_reg;
        rom_en = 1'b0;
        case (c_state)
            S_IDLE: begin
                if (i_frame_start) begin
                    frame_next = i_frame_id;
                    bfp_next = i_bfp_s;
                    count_next = 9'd0;
                    bin_next = 9'd0;
                    band_next = 5'd0;
                    coeff_addr_next = 9'd0;
                    acc_next = 60'd0;
                    overflow_next = 1'b0;
                    error_next = 1'b0;
                    n_state = S_CAPTURE;
                end
            end
            S_CAPTURE: begin
                if (square_valid_reg) begin
                    overflow_next = overflow_reg | square_overflow_reg | power_sum[40];
                    if ((square_bin_reg != count_reg) || (square_last_reg != (count_reg == 9'd511))) begin
                        error_next = 1'b1;
                        n_state = square_last_reg ? S_IDLE : S_DROP;
                    end else begin
                        if (count_reg <= 9'd256) begin
                            ram_en = 1'b1;
                            ram_we = 1'b1;
                            ram_addr = count_reg;
                            power_valid_next = 1'b1;
                            power_next = power_sum[39:0];
                            power_bin_next = count_reg;
                        end
                        if (count_reg == 9'd511) n_state = S_DESC;
                        else count_next = count_reg + 9'd1;
                    end
                end
            end
            S_DESC: begin
                // Generated from frozen integers; all omitted weights are zero.
                bin_next = descriptor_start;
                last_bin_next = descriptor_start + descriptor_length - 9'd1;
                coeff_addr_next = descriptor_offset;
                n_state = S_READ;
            end
            S_READ: begin
                // Synchronous Power/weight read, product, and add overlap.
                // Addresses advance only for an issued nonzero term (II=1).
                ram_en = 1'b1;
                rom_en = 1'b1;
                read_valid_next = 1'b1;
                read_last_next = (bin_reg == last_bin_reg);
                coeff_addr_next = coeff_addr_reg + 9'd1;
                if (bin_reg == last_bin_reg) n_state = S_DRAIN;
                else bin_next = bin_reg + 9'd1;
            end
            S_DRAIN: begin
                // The band is visible only after its last product is added.
                if (product_valid_reg && product_last_reg) n_state = S_OUTPUT;
            end
            S_OUTPUT: begin
                if (i_ready) begin
                    if (band_reg == 5'd25) n_state = S_IDLE;
                    else begin
                        band_next = band_reg + 5'd1;
                        bin_next = 9'd0;
                        acc_next = 60'd0;
                        n_state = S_DESC;
                    end
                end
            end
            S_DROP: begin
                if (square_valid_reg && square_last_reg) n_state = S_IDLE;
            end
            default: begin
                n_state = S_IDLE;
                error_next = 1'b1;
                read_valid_next = 1'b0; read_last_next = 1'b0;
                product_valid_next = 1'b0; product_last_next = 1'b0;
            end
        endcase
        if (product_valid_reg && ((c_state == S_READ) || (c_state == S_DRAIN))) begin
            acc_next = mel_sum[59:0];
            overflow_next = overflow_next | mel_sum[60];
        end
        // There is no credit for an additional FFT frame while busy.
        if (i_frame_start && (c_state != S_IDLE)) error_next = 1'b1;
        if (i_fft_valid && (c_state != S_CAPTURE) && (c_state != S_DROP)) error_next = 1'b1;
        if (square_valid_reg && (c_state != S_CAPTURE) && (c_state != S_DROP)) error_next = 1'b1;
        if (!rst_n) begin
            ram_en = 1'b0;
            ram_we = 1'b0;
            rom_en = 1'b0;
        end
    end
endmodule
