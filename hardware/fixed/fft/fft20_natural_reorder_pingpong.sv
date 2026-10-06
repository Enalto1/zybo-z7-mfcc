// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Two-bank bit-reversed-to-natural-order frame buffer.
// The output has no ready input: every asserted out_valid is consumed that cycle.
module fft20_natural_reorder_pingpong #(
    parameter integer DATA_W       = 16,
    parameter integer MAX_LOG2_N   = 10,
    parameter integer MAX_N        = (1 << MAX_LOG2_N)
) (
    input  wire logic                         clk,
    input  wire logic                         rst_n,
    input  wire logic                         clear,

    input  wire logic [3:0]                   cfg_log2_n,
    input  wire logic                         cfg_apply,
    output logic                              cfg_accept,
    output logic                              cfg_reject,
    output logic [3:0]                        cfg_log2_n_active,
    output logic                              cfg_ready,
    output logic                              idle,

    input  wire logic                         core_valid,
    output logic                              core_ready,
    input  wire logic signed [DATA_W-1:0]     core_re,
    input  wire logic signed [DATA_W-1:0]     core_im,
    input  wire logic [MAX_LOG2_N-1:0]        core_ordinal,
    input  wire logic                         core_last,
    input  wire logic                         core_overflow,

    output logic                              out_valid,
    output logic signed [DATA_W-1:0]          out_re,
    output logic signed [DATA_W-1:0]          out_im,
    output logic [MAX_LOG2_N-1:0]             out_index,
    output logic                              out_last,
    output logic                              out_overflow,

    output logic                              core_frame_error,
    output logic                              bank_collision_error
);
    // E-FFT work-copy memory change: bank bit is part of each address.
    // One synchronous read without reset/output mux permits BRAM inference.
    // read_active and out_valid mask stale memory after reset/clear.
    (* ram_style = "block" *) logic [2*DATA_W-1:0] frame_ram [0:2*MAX_N-1];
    logic [2*DATA_W-1:0] frame_read;

    logic [1:0] bank_full;
    logic [1:0] bank_frame_overflow;
    logic write_bank;
    logic read_bank;
    logic read_active;
    logic [MAX_LOG2_N-1:0] write_count;
    logic [MAX_LOG2_N-1:0] read_count;
    logic write_frame_overflow;
    logic [10:0] frame_length;
    logic [MAX_LOG2_N-1:0] frame_last_index;
    logic [MAX_LOG2_N-1:0] write_address;
    logic core_accept;
    logic write_done;
    logic read_done;

    assign frame_length = 11'd1 << cfg_log2_n_active;
    assign frame_last_index = frame_length[MAX_LOG2_N-1:0] - {{(MAX_LOG2_N-1){1'b0}}, 1'b1};

    fft20_bit_reverse_addr #(.MAX_LOG2_N(MAX_LOG2_N)) write_address_reverse (
        .in_index(core_ordinal),
        .cfg_log2_n(cfg_log2_n_active),
        .out_index(write_address)
    );

    assign idle = (write_count == '0) && !read_active && (bank_full == 2'b00) && !out_valid;
    assign cfg_ready = idle;
    // An accepted idle configuration owns the cycle and must not race a first
    // core sample.  A rejected busy configuration does not disturb streaming.
    assign core_ready = !clear && !bank_full[write_bank] &&
        !(cfg_apply && idle);
    assign core_accept = core_valid && core_ready;
    assign write_done = core_accept && (write_count == frame_last_index);
    assign read_done = read_active && (read_count == frame_last_index);

    assign out_re = $signed(frame_read[2*DATA_W-1:DATA_W]);
    assign out_im = $signed(frame_read[DATA_W-1:0]);

    always_ff @(posedge clk) begin
        if (rst_n && !clear && core_accept)
            frame_ram[{write_bank, write_address}] <= {core_re, core_im};
        if (read_active)
            frame_read <= frame_ram[{read_bank, read_count}];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cfg_accept <= 1'b0;
            cfg_reject <= 1'b0;
            cfg_log2_n_active <= 4'd3;
            bank_full <= 2'b00;
            bank_frame_overflow <= 2'b00;
            write_bank <= 1'b0;
            read_bank <= 1'b0;
            read_active <= 1'b0;
            write_count <= '0;
            read_count <= '0;
            write_frame_overflow <= 1'b0;
            out_valid <= 1'b0;
            out_index <= '0;
            out_last <= 1'b0;
            out_overflow <= 1'b0;
            core_frame_error <= 1'b0;
            bank_collision_error <= 1'b0;
        end else if (clear) begin
            cfg_accept <= 1'b0;
            cfg_reject <= 1'b0;
            bank_full <= 2'b00;
            bank_frame_overflow <= 2'b00;
            write_bank <= 1'b0;
            read_bank <= 1'b0;
            read_active <= 1'b0;
            write_count <= '0;
            read_count <= '0;
            write_frame_overflow <= 1'b0;
            out_valid <= 1'b0;
            out_index <= '0;
            out_last <= 1'b0;
            out_overflow <= 1'b0;
            core_frame_error <= 1'b0;
            bank_collision_error <= 1'b0;
        end else begin
            cfg_accept <= 1'b0;
            cfg_reject <= 1'b0;
            out_valid <= 1'b0;
            out_last <= 1'b0;
            out_overflow <= 1'b0;

            if (cfg_apply) begin
                if (idle && (cfg_log2_n >= 4'd3) && (cfg_log2_n <= MAX_LOG2_N)) begin
                    cfg_log2_n_active <= cfg_log2_n;
                    cfg_accept <= 1'b1;
                end else begin
                    cfg_reject <= 1'b1;
                end
            end

            if (core_valid && !core_ready)
                bank_collision_error <= 1'b1;

            if (core_accept) begin
                if ((core_ordinal != write_count) ||
                    (core_last != (write_count == frame_last_index)))
                    core_frame_error <= 1'b1;

                if (write_done) begin
                    bank_full[write_bank] <= 1'b1;
                    bank_frame_overflow[write_bank] <= write_frame_overflow | core_overflow;
                    write_bank <= ~write_bank;
                    write_count <= '0;
                    write_frame_overflow <= 1'b0;
                end else begin
                    write_count <= write_count + {{(MAX_LOG2_N-1){1'b0}}, 1'b1};
                    write_frame_overflow <= write_frame_overflow | core_overflow;
                end
            end

            if (read_active) begin
                out_valid <= 1'b1;
                out_index <= read_count;
                out_last <= read_done;
                out_overflow <= bank_frame_overflow[read_bank];
                if (read_done) begin
                    bank_full[read_bank] <= 1'b0;
                    read_count <= '0;
                    // write_done is included here so a bank completed on this
                    // same edge becomes the next read bank without a bubble.
                    if (bank_full[~read_bank] ||
                        (write_done && (write_bank != read_bank))) begin
                        read_active <= 1'b1;
                        read_bank <= ~read_bank;
                    end else begin
                        read_active <= 1'b0;
                    end
                end else begin
                    read_count <= read_count + {{(MAX_LOG2_N-1){1'b0}}, 1'b1};
                end
            end else if (write_done) begin
                read_active <= 1'b1;
                read_bank <= write_bank;
                read_count <= '0;
            end

            if (core_accept && read_active && (write_bank == read_bank))
                bank_collision_error <= 1'b1;
        end
    end
endmodule

`default_nettype wire
