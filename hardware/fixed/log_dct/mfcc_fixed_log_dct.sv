module mfcc_fixed_log_dct (
    input logic clk,input logic rst_n,input logic i_valid,output logic o_ready,
    input logic [59:0] i_mel,input logic signed [7:0] i_bfp_s,
    input logic [31:0] i_frame,input logic [4:0] i_index,input logic i_last,input logic i_error,
    output logic o_valid,input logic i_ready,output logic signed [39:0] o_mfcc,
    output logic [31:0] o_frame,output logic [3:0] o_index,
    output logic signed [7:0] o_bfp_s,output logic o_last,output logic o_error
);
    logic log_valid,log_ready,log_last,log_floor,log_error;
    logic signed [29:0] log_value;
    logic [31:0] log_frame;
    logic [4:0] log_index;
    logic signed [5:0] log_bfp,dct_bfp;
    logic input_error;
    assign input_error=i_error||(i_bfp_s < -8'sd2)||(i_bfp_s > 8'sd24);
    assign o_bfp_s={{2{dct_bfp[5]}},dct_bfp};
    mfcc_fixed_log_pair U_LOG (.clk(clk),.rst_n(rst_n),.i_valid(i_valid),.o_ready(o_ready),
        .i_mel(i_mel),.i_bfp_s(i_bfp_s[5:0]),.i_frame(i_frame),.i_index(i_index),.i_last(i_last),.i_error(input_error),
        .o_valid(log_valid),.i_ready(log_ready),.o_log(log_value),.o_frame(log_frame),.o_index(log_index),
        .o_bfp_s(log_bfp),.o_last(log_last),.o_floor(log_floor),.o_error(log_error));
    mfcc_fixed_dct U_DCT (.clk(clk),.rst_n(rst_n),.i_valid(log_valid),.o_ready(log_ready),
        .i_log(log_value),.i_frame(log_frame),.i_index(log_index),.i_bfp_s(log_bfp),.i_last(log_last),.i_error(log_error),
        .o_valid(o_valid),.i_ready(i_ready),.o_mfcc(o_mfcc),.o_frame(o_frame),.o_index(o_index),.o_bfp_s(dct_bfp),.o_last(o_last),.o_error(o_error));
endmodule
