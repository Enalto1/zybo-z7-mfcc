// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Runtime forward-FFT W1024 table with a synchronous read.  The registered
// read lets Vivado infer block memory and separates address generation from
// the following DSP input stage.  Valid metadata is reset in the consumer;
// the ROM data register intentionally has no reset.
module fft20_twiddle_rom_sync #(
    parameter ROM_FILE = "twiddle_1024_w16.mem"
) (
    input  wire logic                  clk,
    input  wire logic [9:0]            address,
    output wire logic signed [15:0]    twiddle_re,
    output wire logic signed [15:0]    twiddle_im
);

    logic [31:0] rom [0:1023];
    logic [31:0] rom_word;

    initial begin
        $readmemh(ROM_FILE, rom);
    end

    always_ff @(posedge clk) begin
        rom_word <= rom[address];
    end

    assign twiddle_re = $signed(rom_word[31:16]);
    assign twiddle_im = $signed(rom_word[15:0]);

endmodule

`default_nettype wire
