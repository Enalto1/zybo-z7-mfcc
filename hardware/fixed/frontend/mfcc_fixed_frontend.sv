// PCM16 -> continuous preemphasis -> overlapping512/160 frames -> Hamming -> BFP.
// Clip start aborts pending work. PCM last discards an incomplete tail; bin last is511.
// Input backpressure is mandatory while a complete frame is processed/drained.
// Two512x32 BRAMs, each one registered read. Window scan and FFT drain have II1.
// Drain shares one advance enable across RAM read, quantization and output;
// a stalled output freezes all occupied stages and their index/valid metadata.
module mfcc_fixed_frontend (
    input logic clk,input logic rst_n,input logic i_clip_start,
    input logic i_pcm_valid,output logic o_pcm_ready,
    input logic signed [15:0] i_pcm,input logic i_pcm_last,
    output logic o_valid,input logic i_ready,output logic signed [15:0] o_fft,
    output logic [31:0] o_frame,output logic [8:0] o_index,
    output logic signed [7:0] o_bfp_s,output logic o_last,output logic o_error,
    output logic o_clip_done
);
    typedef enum logic [2:0] {S_COLLECT,S_PRE_QUOTIENT,S_PRE_CORRECTION,S_PRE_STORE,S_WINDOW_PIPE,S_BFP,S_DRAIN_PIPE} state_t;
    state_t c_state,n_state;
    logic active_reg,active_next,end_reg,end_next,done_reg,done_next;
    logic signed [15:0] previous_reg,previous_next,fft_reg,fft_next;
    logic [8:0] write_reg,write_next,start_reg,start_next,index_reg,index_next;
    logic [9:0] remaining_reg,remaining_next;
    logic [31:0] peak_reg,peak_next,frame_reg,frame_next;
    logic signed [5:0] shift_reg,shift_next;
    logic error_reg,error_next;
    (* ram_style="block" *) logic signed [31:0] window_mem [0:511];
    logic signed [31:0] pre_data_reg,window_data_reg;
    logic signed [32:0] pre_base_reg,pre_base_next;
    logic signed [32:0] pre_correction_reg,pre_correction_next;
    logic [15:0] pre_mag_reg,pre_mag_next;
    logic [13:0] pre_q_reg,pre_q_next;
    logic pre_sign_reg,pre_sign_next,pcm_end_reg,pcm_end_next;
    logic signed [63:0] window_product_reg,window_product_next;
    logic signed [31:0] window_value_reg,window_value_next;
    logic [31:0] window_magnitude_reg,window_magnitude_next;
    logic pre_write,pre_read,window_write,window_read;
    logic [8:0] read_address;
    logic [30:0] window_coefficient;
    logic [30:0] window_coefficient_reg,window_coefficient_next;
    logic [8:0] issue_index_reg,issue_index_next;
    logic issue_done_reg,issue_done_next;
    logic window_read_valid_reg,window_read_valid_next;
    logic window_product_valid_reg,window_product_valid_next;
    logic window_value_valid_reg,window_value_valid_next;
    logic window_magnitude_valid_reg,window_magnitude_valid_next;
    logic [8:0] window_read_index_reg,window_read_index_next;
    logic [8:0] window_product_index_reg,window_product_index_next;
    logic [8:0] window_value_index_reg,window_value_index_next;
    logic [8:0] window_magnitude_index_reg,window_magnitude_index_next;
    logic signed [31:0] window_store_reg,window_store_next;
    logic drain_read_valid_reg,drain_read_valid_next;
    logic drain_quant_valid_reg,drain_quant_valid_next;
    logic output_valid_reg,output_valid_next;
    logic [8:0] drain_read_index_reg,drain_read_index_next;
    logic [8:0] drain_quant_index_reg,drain_quant_index_next;
    logic drain_advance;
    logic [31:0] reciprocal_product;
    logic [15:0] pre_remainder;
    logic [12:0] remainder_correction;
    logic [32:0] correction_magnitude;
    logic signed [32:0] correction,pre_sum;
    logic [8:0] pre_address;
    logic signed [31:0] pre_value;
    logic signed [63:0] window_product;
    logic signed [33:0] window_rounded;
    logic window_round_up;
    logic [31:0] window_magnitude;
    logic bfp_fits;
    logic signed [39:0] quant_input,quant_quotient,quant_rounded;
    logic signed [39:0] quant_quotient_reg,quant_quotient_next;
    logic quant_round_reg,quant_round_next;
    logic [17:0] quant_remainder,quant_mask,quant_half;
    logic [5:0] right_shift;
    logic quant_round_up;
    mfcc_fixed_window_coefficient U_WINDOW (.i_index(issue_index_reg),.o_coefficient(window_coefficient));
    // Exact identity: RNE((20*x-19*p)*32768/20) = (x-p)*32768 + RNE(p*8192/5).
    // floor(m/5)=(m*52429)>>18 is exact for every unsigned16 m; no divider.
    assign reciprocal_product={16'd0,pre_mag_reg}*32'd52429;
    assign pre_remainder=pre_mag_reg-({2'd0,pre_q_reg}<<2)-{2'd0,pre_q_reg};
    assign correction_magnitude=({19'd0,pre_q_reg}<<13)+{20'd0,remainder_correction};
    assign correction=pre_sign_reg ? -$signed(correction_magnitude) : $signed(correction_magnitude);
    assign pre_sum=pre_base_reg+pre_correction_reg;
    assign pre_value=pre_sum[31:0];
    assign pre_address=pre_write ? write_reg : read_address;
    xpm_memory_spram #(
        .MEMORY_SIZE(16384),.MEMORY_PRIMITIVE("block"),.USE_MEM_INIT(0),
        .WRITE_DATA_WIDTH_A(32),.READ_DATA_WIDTH_A(32),.BYTE_WRITE_WIDTH_A(32),
        .ADDR_WIDTH_A(9),.READ_LATENCY_A(1),.WRITE_MODE_A("read_first"),.RST_MODE_A("SYNC")
    ) U_PRE_RAM (
        .sleep(1'b0),.clka(clk),.rsta(1'b0),.ena(pre_read||pre_write),.regcea(1'b1),
        .wea(pre_write),.addra(pre_address),.dina(pre_value),.douta(pre_data_reg),
        .injectsbiterra(1'b0),.injectdbiterra(1'b0),.sbiterra(),.dbiterra()
    );
    assign window_product=$signed(pre_data_reg)*$signed({1'b0,window_coefficient_reg});
    assign window_round_up=(window_product_reg[29:0]>30'h20000000)||((window_product_reg[29:0]==30'h20000000)&&window_product_reg[30]);
    assign window_rounded=$signed(window_product_reg[63:30])+$signed({33'd0,window_round_up});
    assign window_magnitude=window_value_reg[31] ? $unsigned(-window_value_reg) : $unsigned(window_value_reg);
    assign read_address=start_reg+issue_index_reg;
    assign bfp_fits=(shift_reg>=6'sd16) ? (({32'd0,peak_reg} << (shift_reg-6'sd16))<=64'd31949) : ({32'd0,peak_reg}<=(64'd31949 << (6'sd16-shift_reg)));
    // Legal s[-2,24] requires at most 18 right or 8 left bits. A 40-bit
    // quotient is exact for every signed32 input. Mask constants avoid a
    // wide subtractor, and rounding/clamping follows a registered quotient.
    assign quant_input={{8{window_data_reg[31]}},window_data_reg};
    assign right_shift=6'd16-$unsigned(shift_reg);
    assign quant_quotient=(shift_reg<=6'sd16) ? (quant_input >>> right_shift) : (quant_input <<< (shift_reg-6'sd16));
    assign quant_mask=18'h3ffff >> $unsigned(shift_reg+6'sd2);
    assign quant_remainder=window_data_reg[17:0]&quant_mask;
    assign quant_half=18'h20000 >> $unsigned(shift_reg+6'sd2);
    assign quant_round_up=(shift_reg<6'sd16)&&((quant_remainder>quant_half)||((quant_remainder==quant_half)&&quant_quotient[0]));
    assign quant_rounded=quant_quotient_reg+$signed({39'd0,quant_round_reg});
    assign o_pcm_ready=(c_state==S_COLLECT)&&active_reg&&!i_clip_start;
    assign drain_advance=!output_valid_reg||i_ready;
    assign o_valid=(c_state==S_DRAIN_PIPE)&&output_valid_reg;
    assign o_fft=fft_reg;
    assign o_frame=frame_reg;
    assign o_index=index_reg;
    assign o_bfp_s={{2{shift_reg[5]}},shift_reg};
    assign o_last=index_reg==9'd511;
    assign o_error=error_reg;
    assign o_clip_done=done_reg;
    always_ff @(posedge clk) begin
        if(window_write) window_mem[window_magnitude_index_reg]<=window_store_reg;
        if(window_read) window_data_reg<=window_mem[issue_index_reg];
        if(!rst_n) begin
            c_state<=S_COLLECT;active_reg<=1'b0;end_reg<=1'b0;done_reg<=1'b0;
            previous_reg<=16'sd0;fft_reg<=16'sd0;write_reg<=9'd0;start_reg<=9'd0;index_reg<=9'd0;
            remaining_reg<=10'd512;peak_reg<=32'd0;frame_reg<=32'd0;shift_reg<=6'sd0;error_reg<=1'b0;
            pre_base_reg<=33'sd0;pre_mag_reg<=16'd0;pre_q_reg<=14'd0;pre_sign_reg<=1'b0;pcm_end_reg<=1'b0;window_product_reg<=64'sd0;
            quant_quotient_reg<=40'sd0;quant_round_reg<=1'b0;
            window_value_reg<=32'sd0;window_magnitude_reg<=32'd0;
            pre_correction_reg<=33'sd0;
            window_coefficient_reg<=31'd0;
            issue_index_reg<=9'd0;issue_done_reg<=1'b0;
            window_read_valid_reg<=1'b0;window_product_valid_reg<=1'b0;
            window_value_valid_reg<=1'b0;window_magnitude_valid_reg<=1'b0;
            window_read_index_reg<=9'd0;window_product_index_reg<=9'd0;
            window_value_index_reg<=9'd0;window_magnitude_index_reg<=9'd0;
            window_store_reg<=32'sd0;
            drain_read_valid_reg<=1'b0;drain_quant_valid_reg<=1'b0;output_valid_reg<=1'b0;
            drain_read_index_reg<=9'd0;drain_quant_index_reg<=9'd0;
        end else begin
            c_state<=n_state;active_reg<=active_next;end_reg<=end_next;done_reg<=done_next;
            previous_reg<=previous_next;fft_reg<=fft_next;write_reg<=write_next;start_reg<=start_next;index_reg<=index_next;
            remaining_reg<=remaining_next;peak_reg<=peak_next;frame_reg<=frame_next;shift_reg<=shift_next;error_reg<=error_next;
            pre_base_reg<=pre_base_next;pre_mag_reg<=pre_mag_next;pre_q_reg<=pre_q_next;pre_sign_reg<=pre_sign_next;pcm_end_reg<=pcm_end_next;window_product_reg<=window_product_next;
            quant_quotient_reg<=quant_quotient_next;quant_round_reg<=quant_round_next;
            window_value_reg<=window_value_next;window_magnitude_reg<=window_magnitude_next;
            pre_correction_reg<=pre_correction_next;
            window_coefficient_reg<=window_coefficient_next;
            issue_index_reg<=issue_index_next;issue_done_reg<=issue_done_next;
            window_read_valid_reg<=window_read_valid_next;window_product_valid_reg<=window_product_valid_next;
            window_value_valid_reg<=window_value_valid_next;window_magnitude_valid_reg<=window_magnitude_valid_next;
            window_read_index_reg<=window_read_index_next;window_product_index_reg<=window_product_index_next;
            window_value_index_reg<=window_value_index_next;window_magnitude_index_reg<=window_magnitude_index_next;
            window_store_reg<=window_store_next;
            drain_read_valid_reg<=drain_read_valid_next;drain_quant_valid_reg<=drain_quant_valid_next;output_valid_reg<=output_valid_next;
            drain_read_index_reg<=drain_read_index_next;drain_quant_index_reg<=drain_quant_index_next;
        end
    end
    always_comb begin
        n_state=c_state;active_next=active_reg;end_next=end_reg;done_next=1'b0;
        previous_next=previous_reg;fft_next=fft_reg;write_next=write_reg;start_next=start_reg;index_next=index_reg;
        remaining_next=remaining_reg;peak_next=peak_reg;frame_next=frame_reg;shift_next=shift_reg;error_next=error_reg;
        pre_base_next=pre_base_reg;pre_mag_next=pre_mag_reg;pre_q_next=pre_q_reg;pre_sign_next=pre_sign_reg;pcm_end_next=pcm_end_reg;window_product_next=window_product_reg;
        quant_quotient_next=quant_quotient_reg;quant_round_next=quant_round_reg;
        window_value_next=window_value_reg;window_magnitude_next=window_magnitude_reg;
        pre_correction_next=pre_correction_reg;
        window_coefficient_next=window_coefficient_reg;
        issue_index_next=issue_index_reg;issue_done_next=issue_done_reg;
        window_read_valid_next=1'b0;window_product_valid_next=1'b0;
        window_value_valid_next=1'b0;window_magnitude_valid_next=1'b0;
        window_read_index_next=window_read_index_reg;window_product_index_next=window_product_index_reg;
        window_value_index_next=window_value_index_reg;window_magnitude_index_next=window_magnitude_index_reg;
        window_store_next=window_store_reg;
        drain_read_valid_next=drain_read_valid_reg;drain_quant_valid_next=drain_quant_valid_reg;
        output_valid_next=output_valid_reg;
        drain_read_index_next=drain_read_index_reg;drain_quant_index_next=drain_quant_index_reg;
        pre_write=1'b0;pre_read=1'b0;window_write=1'b0;window_read=1'b0;
        remainder_correction=13'd0;
        case(pre_remainder)
            16'd1: remainder_correction=13'd1638;
            16'd2: remainder_correction=13'd3277;
            16'd3: remainder_correction=13'd4915;
            16'd4: remainder_correction=13'd6554;
            default: remainder_correction=13'd0;
        endcase
        case(c_state)
            S_COLLECT: if(i_pcm_valid&&o_pcm_ready) begin
                pre_base_next=($signed({{17{i_pcm[15]}},i_pcm})-$signed({{17{previous_reg[15]}},previous_reg})) <<< 15;
                pre_mag_next=previous_reg[15] ? $unsigned(-previous_reg) : $unsigned(previous_reg);
                pre_sign_next=previous_reg[15];pcm_end_next=i_pcm_last;previous_next=i_pcm;
                n_state=S_PRE_QUOTIENT;
            end
            S_PRE_QUOTIENT: begin pre_q_next=reciprocal_product[31:18];n_state=S_PRE_CORRECTION;end
            S_PRE_CORRECTION: begin pre_correction_next=correction;n_state=S_PRE_STORE;end
            S_PRE_STORE: begin
                pre_write=rst_n;write_next=write_reg+9'd1;n_state=S_COLLECT;
                remaining_next=remaining_reg-10'd1;
                if(remaining_reg==10'd1) begin
                    start_next=write_reg+9'd1;index_next=9'd0;peak_next=32'd0;
                    issue_index_next=9'd0;issue_done_next=1'b0;
                    error_next=1'b0;end_next=pcm_end_reg;n_state=S_WINDOW_PIPE;
                end else if(pcm_end_reg) begin
                    active_next=1'b0;previous_next=16'sd0;remaining_next=10'd512;done_next=1'b1;
                end
            end
            S_WINDOW_PIPE: begin
                // Read/constant, full product, RNE, magnitude and peak/write
                // are independent registered stages. Index accompanies data.
                if(!issue_done_reg) begin
                    pre_read=rst_n;window_coefficient_next=window_coefficient;
                    window_read_valid_next=1'b1;window_read_index_next=issue_index_reg;
                    if(issue_index_reg==9'd511) issue_done_next=1'b1;
                    else issue_index_next=issue_index_reg+9'd1;
                end
                window_product_valid_next=window_read_valid_reg;
                if(window_read_valid_reg) begin
                    window_product_next=window_product;
                    window_product_index_next=window_read_index_reg;
                end
                window_value_valid_next=window_product_valid_reg;
                if(window_product_valid_reg) begin
                    window_value_next=window_rounded[31:0];window_value_index_next=window_product_index_reg;
                    if(window_rounded[33:31]!={3{window_rounded[31]}}) error_next=1'b1;
                end
                window_magnitude_valid_next=window_value_valid_reg;
                if(window_value_valid_reg) begin
                    window_magnitude_next=window_magnitude;window_store_next=window_value_reg;
                    window_magnitude_index_next=window_value_index_reg;
                end
                if(window_magnitude_valid_reg) begin
                    window_write=rst_n;
                    if(window_magnitude_reg>peak_reg) peak_next=window_magnitude_reg;
                    if(window_magnitude_index_reg==9'd511) begin shift_next=6'sd24;n_state=S_BFP;end
                end
            end
            S_BFP: begin
                if(peak_reg==32'd0) begin
                    shift_next=6'sd0;index_next=9'd0;issue_index_next=9'd0;issue_done_next=1'b0;n_state=S_DRAIN_PIPE;
                end else if(bfp_fits || shift_reg==-6'sd2) begin
                    index_next=9'd0;issue_index_next=9'd0;issue_done_next=1'b0;n_state=S_DRAIN_PIPE;
                end
                else shift_next=shift_reg-6'sd1;
            end
            S_DRAIN_PIPE: begin
                if(drain_advance) begin
                    // Freeze the RAM read register as well as arithmetic when
                    // output stalls. No extra read response can overwrite data.
                    drain_read_valid_next=!issue_done_reg;
                    if(!issue_done_reg) begin
                        window_read=rst_n;drain_read_index_next=issue_index_reg;
                        if(issue_index_reg==9'd511) issue_done_next=1'b1;
                        else issue_index_next=issue_index_reg+9'd1;
                    end
                    drain_quant_valid_next=drain_read_valid_reg;
                    if(drain_read_valid_reg) begin
                        quant_quotient_next=quant_quotient;quant_round_next=quant_round_up;
                        drain_quant_index_next=drain_read_index_reg;
                    end
                    output_valid_next=drain_quant_valid_reg;
                    if(drain_quant_valid_reg) begin
                        index_next=drain_quant_index_reg;
                        if(quant_rounded>40'sd32767) begin fft_next=16'sd32767;error_next=1'b1;end
                        else if(quant_rounded < -40'sd32767) begin fft_next=-16'sd32767;error_next=1'b1;end
                        else fft_next=quant_rounded[15:0];
                    end
                    if(output_valid_reg&&i_ready&&(index_reg==9'd511)) begin
                        drain_read_valid_next=1'b0;drain_quant_valid_next=1'b0;output_valid_next=1'b0;
                        frame_next=frame_reg+32'd1;remaining_next=10'd160;n_state=S_COLLECT;
                        if(end_reg) begin active_next=1'b0;previous_next=16'sd0;done_next=1'b1;end
                    end
                end
            end
            default: begin
                n_state=S_COLLECT;active_next=1'b0;error_next=1'b1;
                drain_read_valid_next=1'b0;drain_quant_valid_next=1'b0;output_valid_next=1'b0;
            end
        endcase
        if(i_clip_start) begin
            n_state=S_COLLECT;active_next=!(i_pcm_last&&!i_pcm_valid);previous_next=16'sd0;
            write_next=9'd0;start_next=9'd0;index_next=9'd0;remaining_next=10'd512;
            frame_next=32'd0;end_next=1'b0;error_next=1'b0;done_next=i_pcm_last&&!i_pcm_valid;
            issue_index_next=9'd0;issue_done_next=1'b0;
            window_read_valid_next=1'b0;window_product_valid_next=1'b0;
            window_value_valid_next=1'b0;window_magnitude_valid_next=1'b0;
            drain_read_valid_next=1'b0;drain_quant_valid_next=1'b0;output_valid_next=1'b0;
            pre_write=1'b0;pre_read=1'b0;window_write=1'b0;window_read=1'b0;
        end
    end
endmodule
