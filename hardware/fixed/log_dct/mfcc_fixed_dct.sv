// Sequential orthonormal 26->13 DCT. Coefficients signed31/F30; log signed30/F24.
// Product signed61, accumulator signed64; output signed40/F24, one RNE>>30.
// One 26-word BRAM with synchronous read; no RAM reset or array next copy.
module mfcc_fixed_dct (
    input logic clk, input logic rst_n,
    input logic i_valid, output logic o_ready,
    input logic signed [29:0] i_log,
    input logic [31:0] i_frame, input logic [4:0] i_index,
    input logic signed [5:0] i_bfp_s, input logic i_last,input logic i_error,
    output logic o_valid,input logic i_ready,
    output logic signed [39:0] o_mfcc,
    output logic [31:0] o_frame,output logic [3:0] o_index,
    output logic signed [5:0] o_bfp_s,output logic o_last,output logic o_error
);
    typedef enum logic [2:0] {S_COLLECT,S_READ,S_PRODUCT,S_MAC,S_ROUND,S_OUTPUT} state_t;
    state_t c_state,n_state;
    logic [4:0] sample_reg,sample_next;
    logic [3:0] coef_reg,coef_next;
    logic signed [63:0] accum_reg,accum_next;
    logic signed [39:0] mfcc_reg,mfcc_next;
    logic [31:0] frame_reg,frame_next;
    logic signed [5:0] bfp_reg,bfp_next;
    logic error_reg,error_next;
    (* ram_style="block" *) logic signed [29:0] log_mem [0:31];
    logic signed [29:0] memory_data_reg;
    logic mem_write,mem_read;
    logic signed [30:0] coefficient;
    logic signed [30:0] coefficient_reg,coefficient_next;
    logic signed [60:0] product_reg,product_next;
    logic signed [60:0] product;
    logic signed [63:0] sum;
    logic signed [34:0] rounded;
    logic round_up;
    mfcc_fixed_dct_coefficient U_COEFFICIENT (.i_coef(coef_reg),.i_sample(sample_reg),.o_coefficient(coefficient));
    assign product=$signed(memory_data_reg)*$signed(coefficient_reg);
    assign sum=accum_reg+{{3{product_reg[60]}},product_reg};
    assign round_up=(accum_reg[29:0]>30'h20000000)||((accum_reg[29:0]==30'h20000000)&&accum_reg[30]);
    assign rounded=$signed({accum_reg[63],accum_reg[63:30]})+$signed({34'd0,round_up});
    assign o_ready=c_state==S_COLLECT;
    assign o_valid=c_state==S_OUTPUT;
    assign o_mfcc=mfcc_reg;
    assign o_frame=frame_reg;
    assign o_index=coef_reg;
    assign o_bfp_s=bfp_reg;
    assign o_last=coef_reg==4'd12;
    assign o_error=error_reg;
    always_ff @(posedge clk) begin
        if(mem_write) log_mem[sample_reg] <= i_log;
        if(mem_read) memory_data_reg <= log_mem[sample_reg];
        if(!rst_n) begin
            c_state<=S_COLLECT;sample_reg<=5'd0;coef_reg<=4'd0;
            accum_reg<=64'sd0;mfcc_reg<=40'sd0;frame_reg<=32'd0;
            bfp_reg<=6'sd0;error_reg<=1'b0;
            coefficient_reg<=31'sd0;product_reg<=61'sd0;
        end else begin
            c_state<=n_state;sample_reg<=sample_next;coef_reg<=coef_next;
            accum_reg<=accum_next;mfcc_reg<=mfcc_next;frame_reg<=frame_next;
            bfp_reg<=bfp_next;error_reg<=error_next;
            coefficient_reg<=coefficient_next;product_reg<=product_next;
        end
    end
    always_comb begin
        n_state=c_state;sample_next=sample_reg;coef_next=coef_reg;
        accum_next=accum_reg;mfcc_next=mfcc_reg;frame_next=frame_reg;
        bfp_next=bfp_reg;error_next=error_reg;mem_read=1'b0;mem_write=1'b0;
        coefficient_next=coefficient_reg;product_next=product_reg;
        case(c_state)
            S_COLLECT: if(i_valid) begin
                mem_write=rst_n;
                if(sample_reg==5'd0) begin
                    frame_next=i_frame;bfp_next=i_bfp_s;error_next=i_error;
                end else if(i_error || i_frame!=frame_reg || i_bfp_s!=bfp_reg) error_next=1'b1;
                if(i_index!=sample_reg || i_last!=(sample_reg==5'd25)) error_next=1'b1;
                if(sample_reg==5'd25) begin
                    sample_next=5'd0;coef_next=4'd0;accum_next=64'sd0;n_state=S_READ;
                end else sample_next=sample_reg+5'd1;
            end
            S_READ: begin mem_read=rst_n;coefficient_next=coefficient;n_state=S_PRODUCT;end
            S_PRODUCT: begin product_next=product;n_state=S_MAC;end
            S_MAC: begin
                accum_next=sum;
                if(sample_reg==5'd25) begin
                    n_state=S_ROUND;
                end else begin sample_next=sample_reg+5'd1;n_state=S_READ;end
            end
            S_ROUND: begin mfcc_next={{5{rounded[34]}},rounded};n_state=S_OUTPUT;end
            S_OUTPUT: if(i_ready) begin
                sample_next=5'd0;accum_next=64'sd0;
                if(coef_reg==4'd12) begin n_state=S_COLLECT;coef_next=4'd0;end
                else begin coef_next=coef_reg+4'd1;n_state=S_READ;end
            end
            default: begin n_state=S_COLLECT;sample_next=5'd0;error_next=1'b1;end
        endcase
    end
endmodule
