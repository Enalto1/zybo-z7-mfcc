// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Reverse only the cfg_log2_n least-significant bits.  Higher bits are zero.
module fft20_bit_reverse_addr #(
    parameter integer MAX_LOG2_N = 10
) (
    input  wire logic [MAX_LOG2_N-1:0] in_index,
    input  wire logic [3:0]              cfg_log2_n,
    output logic [MAX_LOG2_N-1:0]        out_index
);
    integer bit_number;

    always_comb begin
        out_index = '0;
        for (bit_number = 0; bit_number < MAX_LOG2_N; bit_number = bit_number + 1) begin
            if (bit_number < cfg_log2_n)
                out_index[bit_number] = in_index[cfg_log2_n - 1 - bit_number];
        end
    end
endmodule

`default_nettype wire
