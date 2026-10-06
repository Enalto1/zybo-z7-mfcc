// Ordered pair of unchanged integer logarithm lanes; see DESIGN.md.
module mfcc_fixed_log_pair (
    input  logic clk,
    input  logic rst_n,
    input  logic i_valid,
    output logic o_ready,
    input  logic [59:0] i_mel,
    input  logic signed [5:0] i_bfp_s,
    input  logic [31:0] i_frame,
    input  logic [4:0] i_index,
    input  logic i_last,
    input  logic i_error,
    output logic o_valid,
    input  logic i_ready,
    output logic signed [29:0] o_log,
    output logic [31:0] o_frame,
    output logic [4:0] o_index,
    output logic signed [5:0] o_bfp_s,
    output logic o_last,
    output logic o_floor,
    output logic o_error
);
    logic input_lane_reg, input_lane_next;
    logic output_lane_reg, output_lane_next;
    logic lane0_input_valid, lane1_input_valid;
    logic lane0_input_ready, lane1_input_ready;
    logic lane0_output_valid, lane1_output_valid;
    logic lane0_output_ready, lane1_output_ready;
    logic signed [29:0] lane0_log, lane1_log;
    logic [31:0] lane0_frame, lane1_frame;
    logic [4:0] lane0_index, lane1_index;
    logic signed [5:0] lane0_bfp, lane1_bfp;
    logic lane0_last, lane1_last;
    logic lane0_floor, lane1_floor;
    logic lane0_error, lane1_error;

    assign o_ready = rst_n && (input_lane_reg ? lane1_input_ready : lane0_input_ready);
    assign lane0_input_valid = rst_n && i_valid && !input_lane_reg;
    assign lane1_input_valid = rst_n && i_valid && input_lane_reg;
    assign lane0_output_ready = rst_n && i_ready && !output_lane_reg;
    assign lane1_output_ready = rst_n && i_ready && output_lane_reg;
    assign o_valid = rst_n && (output_lane_reg ? lane1_output_valid : lane0_output_valid);
    assign o_log = output_lane_reg ? lane1_log : lane0_log;
    assign o_frame = output_lane_reg ? lane1_frame : lane0_frame;
    assign o_index = output_lane_reg ? lane1_index : lane0_index;
    assign o_bfp_s = output_lane_reg ? lane1_bfp : lane0_bfp;
    assign o_last = output_lane_reg ? lane1_last : lane0_last;
    assign o_floor = output_lane_reg ? lane1_floor : lane0_floor;
    assign o_error = output_lane_reg ? lane1_error : lane0_error;

    mfcc_fixed_log U_LOG0 (
        .clk(clk), .rst_n(rst_n),
        .i_valid(lane0_input_valid), .o_ready(lane0_input_ready),
        .i_mel(i_mel), .i_bfp_s(i_bfp_s), .i_frame(i_frame), .i_index(i_index),
        .i_last(i_last), .i_error(i_error),
        .o_valid(lane0_output_valid), .i_ready(lane0_output_ready),
        .o_log(lane0_log), .o_frame(lane0_frame), .o_index(lane0_index),
        .o_bfp_s(lane0_bfp), .o_last(lane0_last),
        .o_floor(lane0_floor), .o_error(lane0_error)
    );
    mfcc_fixed_log U_LOG1 (
        .clk(clk), .rst_n(rst_n),
        .i_valid(lane1_input_valid), .o_ready(lane1_input_ready),
        .i_mel(i_mel), .i_bfp_s(i_bfp_s), .i_frame(i_frame), .i_index(i_index),
        .i_last(i_last), .i_error(i_error),
        .o_valid(lane1_output_valid), .i_ready(lane1_output_ready),
        .o_log(lane1_log), .o_frame(lane1_frame), .o_index(lane1_index),
        .o_bfp_s(lane1_bfp), .o_last(lane1_last),
        .o_floor(lane1_floor), .o_error(lane1_error)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            input_lane_reg <= 1'b0;
            output_lane_reg <= 1'b0;
        end else begin
            input_lane_reg <= input_lane_next;
            output_lane_reg <= output_lane_next;
        end
    end

    always_comb begin
        input_lane_next = input_lane_reg;
        output_lane_next = output_lane_reg;
        if (i_valid && o_ready) input_lane_next = !input_lane_reg;
        if (o_valid && i_ready) output_lane_next = !output_lane_reg;
    end
endmodule
