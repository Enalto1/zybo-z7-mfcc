// Integer natural log contract v2: unsigned60 Mel, E=T*2^(-43-2*s).
// No runtime real arithmetic. One transaction in flight; output stalls supported.
module mfcc_fixed_log (
    input logic clk, input logic rst_n,
    input logic i_valid, output logic o_ready,
    input logic [59:0] i_mel, input logic signed [5:0] i_bfp_s,
    input logic [31:0] i_frame, input logic [4:0] i_index, input logic i_last,
    input logic i_error,
    output logic o_valid, input logic i_ready,
    output logic signed [29:0] o_log,
    output logic [31:0] o_frame, output logic [4:0] o_index,
    output logic signed [5:0] o_bfp_s, output logic o_last,
    output logic o_floor, output logic o_error
);
    typedef enum logic [2:0] {S_IDLE,S_NORMALIZE,S_SQUARE_PARTIAL,S_SQUARE_COMBINE,S_SQUARE,S_LOG_PRODUCT,S_CONVERT,S_OUTPUT} state_t;
    state_t c_state,n_state;
    logic [59:0] mantissa_reg,mantissa_next;
    logic signed [8:0] exponent_reg,exponent_next;
    logic [29:0] fraction_reg,fraction_next;
    logic [4:0] iteration_reg,iteration_next;
    logic signed [29:0] log_reg,log_next;
    logic [31:0] frame_reg,frame_next;
    logic [4:0] index_reg,index_next;
    logic signed [5:0] bfp_reg,bfp_next;
    logic last_reg,last_next,floor_reg,floor_next,error_reg,error_next;
    logic [59:0] threshold;
    logic [63:0] square;
    logic [63:0] square_reg,square_next;
    logic [31:0] square_hi_reg,square_hi_next,square_lo_reg,square_lo_next;
    logic [31:0] square_cross_reg,square_cross_next;
    logic signed [39:0] log2_value;
    logic signed [70:0] log_product;
    logic signed [70:0] log_product_reg,log_product_next;
    logic signed [34:0] rounded_log;
    logic round_up;

    mfcc_fixed_floor_threshold U_FLOOR_THRESHOLD (.i_bfp_s(i_bfp_s),.o_threshold(threshold));
    // Exact unsigned32 square: three registered unsigned16 products, then
    // one registered 64-bit sum. No truncation occurs before normalization.
    assign square = {square_hi_reg,32'd0}+{15'd0,square_cross_reg,17'd0}+{32'd0,square_lo_reg};
    assign log2_value = ($signed({{31{exponent_reg[8]}},exponent_reg}) <<< 30) + $signed({10'd0,fraction_reg});
    assign log_product = $signed(log2_value) * 31'sd744261118;
    assign round_up = (log_product_reg[35:0] > 36'h800000000) || ((log_product_reg[35:0] == 36'h800000000) && log_product_reg[36]);
    assign rounded_log = $signed(log_product_reg[70:36]) + $signed({34'd0,round_up});
    assign o_ready = c_state == S_IDLE;
    assign o_valid = c_state == S_OUTPUT;
    assign o_log = log_reg;
    assign o_frame = frame_reg;
    assign o_index = index_reg;
    assign o_bfp_s = bfp_reg;
    assign o_last = last_reg;
    assign o_floor = floor_reg;
    assign o_error = error_reg;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            c_state <= S_IDLE; mantissa_reg <= 60'd0; exponent_reg <= 9'sd0;
            fraction_reg <= 30'd0; iteration_reg <= 5'd0; log_reg <= 30'sd0;
            frame_reg <= 32'd0; index_reg <= 5'd0; bfp_reg <= 6'sd0;
            last_reg <= 1'b0; floor_reg <= 1'b0; error_reg <= 1'b0;
            log_product_reg <= 71'sd0;
            square_reg<=64'd0;square_hi_reg<=32'd0;square_lo_reg<=32'd0;square_cross_reg<=32'd0;
        end else begin
            c_state <= n_state; mantissa_reg <= mantissa_next; exponent_reg <= exponent_next;
            fraction_reg <= fraction_next; iteration_reg <= iteration_next; log_reg <= log_next;
            frame_reg <= frame_next; index_reg <= index_next; bfp_reg <= bfp_next;
            last_reg <= last_next; floor_reg <= floor_next; error_reg <= error_next;
            log_product_reg <= log_product_next;
            square_reg<=square_next;square_hi_reg<=square_hi_next;square_lo_reg<=square_lo_next;square_cross_reg<=square_cross_next;
        end
    end
    always_comb begin
        n_state=c_state; mantissa_next=mantissa_reg; exponent_next=exponent_reg;
        fraction_next=fraction_reg; iteration_next=iteration_reg; log_next=log_reg;
        frame_next=frame_reg; index_next=index_reg; bfp_next=bfp_reg;
        last_next=last_reg; floor_next=floor_reg; error_next=error_reg;
        log_product_next=log_product_reg;
        square_next=square_reg;square_hi_next=square_hi_reg;square_lo_next=square_lo_reg;square_cross_next=square_cross_reg;
        case(c_state)
            S_IDLE: begin
                if(i_valid) begin
                    frame_next=i_frame; index_next=i_index; bfp_next=i_bfp_s;
                    last_next=i_last; error_next=i_error; floor_next=1'b0;
                    fraction_next=30'd0; iteration_next=5'd0;
                    if ((i_bfp_s < -6'sd2) || (i_bfp_s > 6'sd24)) begin
                        log_next=30'sd0; error_next=1'b1; n_state=S_OUTPUT;
                    end else if (i_mel < threshold) begin
                        log_next=-30'sd463571610; floor_next=1'b1; n_state=S_OUTPUT;
                    end else begin
                        mantissa_next=i_mel;
                        exponent_next=-9'sd12-($signed({{3{i_bfp_s[5]}},i_bfp_s}) <<< 1);
                        n_state=S_NORMALIZE;
                    end
                end
            end
            S_NORMALIZE: begin
                if(mantissa_reg >= 60'd4294967296) begin
                    mantissa_next=mantissa_reg >> 1; exponent_next=exponent_reg+9'sd1;
                end else if(mantissa_reg < 60'd2147483648) begin
                    mantissa_next=mantissa_reg << 1; exponent_next=exponent_reg-9'sd1;
                end else n_state=S_SQUARE_PARTIAL;
            end
            S_SQUARE_PARTIAL: begin
                square_hi_next={16'd0,mantissa_reg[31:16]}*{16'd0,mantissa_reg[31:16]};
                square_lo_next={16'd0,mantissa_reg[15:0]}*{16'd0,mantissa_reg[15:0]};
                square_cross_next={16'd0,mantissa_reg[31:16]}*{16'd0,mantissa_reg[15:0]};
                n_state=S_SQUARE_COMBINE;
            end
            S_SQUARE_COMBINE: begin
                square_next=square;n_state=S_SQUARE;
            end
            S_SQUARE: begin
                if(square_reg[63]) begin
                    mantissa_next={28'd0,square_reg[63:32]};
                    fraction_next={fraction_reg[28:0],1'b1};
                end else begin
                    mantissa_next={28'd0,square_reg[62:31]};
                    fraction_next={fraction_reg[28:0],1'b0};
                end
                if(iteration_reg==5'd29) n_state=S_LOG_PRODUCT;
                else begin iteration_next=iteration_reg+5'd1;n_state=S_SQUARE_PARTIAL;end
            end
            S_LOG_PRODUCT: begin log_product_next=log_product;n_state=S_CONVERT;end
            S_CONVERT: begin
                log_next=rounded_log[29:0];
                if(rounded_log[34:29] != {6{rounded_log[29]}}) error_next=1'b1;
                n_state=S_OUTPUT;
            end
            S_OUTPUT: if(i_ready) n_state=S_IDLE;
            default: begin
                n_state=S_IDLE; error_next=1'b1;
            end
        endcase
    end
endmodule
