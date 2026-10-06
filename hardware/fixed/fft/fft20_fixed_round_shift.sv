// E-FFT work copy: see PROVENANCE.json. Original is preserved.
`timescale 1ns/1ps
`default_nettype none

// Signed integer requantizer. SHIFT removes fractional LSBs; the output wraps
// to OUT_W and overflow reports loss of a valid sign extension.
module fft20_fixed_round_shift #(
    parameter integer IN_W             = 35,
    parameter integer OUT_W            = 18,
    parameter integer SHIFT            = 15,
    parameter integer ROUND_CONVERGENT = 1
) (
    input  wire logic signed [IN_W-1:0] in_data,
    output logic signed [OUT_W-1:0] out_data,
    output logic                    overflow
);

    localparam integer EXT_W = IN_W + 1;

    logic signed [EXT_W-1:0] value_ext;
    logic signed [EXT_W-1:0] rounded_value;
    logic signed [EXT_W-1:0] output_sign_extended;

    assign value_ext = {in_data[IN_W-1], in_data};

    generate
        if (SHIFT == 0) begin : g_no_shift
            always_comb begin
                rounded_value = value_ext;
            end
        end else if (ROUND_CONVERGENT != 0) begin : g_convergent
            // Arithmetic shift gives floor(value/2^SHIFT).  The discarded
            // two's-complement bits are its non-negative remainder, so the
            // round bit, sticky bits, and quotient LSB implement ties-to-even
            // for positive and negative inputs without an abs/negate chain.
            localparam integer QUOT_W = EXT_W - SHIFT;
            logic signed [QUOT_W-1:0] floor_quotient;
            logic signed [QUOT_W-1:0] rounded_quotient;
            logic                     increment;

            if (SHIFT == 1) begin : g_one_discarded_bit
                always_comb begin
                    floor_quotient = $signed(value_ext[EXT_W-1:SHIFT]);
                    increment = value_ext[0] && floor_quotient[0];
                    rounded_quotient = floor_quotient +
                        {{(QUOT_W-1){1'b0}}, increment};
                    rounded_value =
                        {{SHIFT{rounded_quotient[QUOT_W-1]}},
                         rounded_quotient};
                end
            end else begin : g_multiple_discarded_bits
                always_comb begin
                    floor_quotient = $signed(value_ext[EXT_W-1:SHIFT]);
                    increment = value_ext[SHIFT-1] &&
                        ((|value_ext[SHIFT-2:0]) || floor_quotient[0]);
                    rounded_quotient = floor_quotient +
                        {{(QUOT_W-1){1'b0}}, increment};
                    rounded_value =
                        {{SHIFT{rounded_quotient[QUOT_W-1]}},
                         rounded_quotient};
                end
            end
        end else begin : g_truncation
            always_comb begin
                rounded_value = value_ext >>> SHIFT;
            end
        end
    endgenerate

    assign out_data = rounded_value[OUT_W-1:0];
    assign output_sign_extended =
        {{(EXT_W-OUT_W){out_data[OUT_W-1]}}, out_data};
    assign overflow = (rounded_value != output_sign_extended);

endmodule

`default_nettype wire
