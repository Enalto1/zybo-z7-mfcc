// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Runtime form of p=(q-D) mod 4D, r={0,2,1,3}, n3=p mod D.
// The local W_(4D) exponent is scaled into the shared W_1024 ROM.
module fft20_r22sdf_runtime_twiddle_addr_gen (
    input  wire logic [3:0] delay_log2,
    input  wire logic [9:0] local_ordinal,
    output logic [9:0]      rom_address
);
    logic [9:0] delay_mask;
    logic [3:0] root_shift;
    logic [1:0] stream_block;
    logic [1:0] branch_digit;
    logic [9:0] n3;
    logic [11:0] local_exponent;
    logic [21:0] root_exponent;

    always_comb begin
        delay_mask = (10'd1 << delay_log2) - 10'd1;
        root_shift = 4'd8 - delay_log2;
        stream_block = (local_ordinal >> delay_log2) & 2'b11;
        case (stream_block)
            2'd0: branch_digit = 2'd0;
            2'd1: branch_digit = 2'd2;
            2'd2: branch_digit = 2'd1;
            default: branch_digit = 2'd3;
        endcase
        n3 = local_ordinal & delay_mask;
        local_exponent = branch_digit * n3;
        root_exponent = local_exponent << root_shift;
        rom_address = root_exponent[9:0];
    end
endmodule

`default_nettype wire
