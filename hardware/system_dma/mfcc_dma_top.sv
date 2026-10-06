`default_nettype none
// Wiring-only DMA wrapper. CORE_KIND selects one frozen arithmetic core.
// Explicit wire inputs satisfy default_nettype none and Vivado IP packager's
// scalar-port parser; internal and output data retain SystemVerilog logic.
module mfcc_dma_top #(
    parameter integer CORE_KIND = 1,
    parameter MEL_INIT_FILE = "mel_fw16.mem"
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:S_AXIS_PCM:M_AXIS_RESULT, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 100000000" *)
    input wire s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire s_axi_aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 16, FREQ_HZ 100000000" *)
    input wire [15:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output logic s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output logic s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output logic [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output logic s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [15:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output logic s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output logic [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output logic [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output logic s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire s_axi_rready,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_PCM TDATA" *)
    (* X_INTERFACE_PARAMETER = "FREQ_HZ 100000000, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1" *)
    input wire [31:0] s_axis_pcm_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_PCM TKEEP" *)
    input wire [3:0] s_axis_pcm_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_PCM TLAST" *)
    input wire  s_axis_pcm_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_PCM TVALID" *)
    input wire  s_axis_pcm_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_PCM TREADY" *)
    output logic  s_axis_pcm_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_RESULT TDATA" *)
    (* X_INTERFACE_PARAMETER = "FREQ_HZ 100000000, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1" *)
    output logic [31:0] m_axis_result_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_RESULT TKEEP" *)
    output logic [3:0] m_axis_result_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_RESULT TLAST" *)
    output logic  m_axis_result_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_RESULT TVALID" *)
    output logic  m_axis_result_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_RESULT TREADY" *)
    input wire  m_axis_result_tready,
    (* X_INTERFACE_INFO = "xilinx.com:signal:interrupt:1.0 irq INTERRUPT" *)
    (* X_INTERFACE_PARAMETER = "SENSITIVITY LEVEL_HIGH" *)
    output logic irq
);
    logic clip_start, abort_core, core_rst_n;
    logic pcm_valid, pcm_ready, pcm_last;
    logic signed [15:0] pcm;
    logic output_valid, output_ready, output_last, output_error, metadata_error, clip_done;
    logic [63:0] output_payload;
    logic [31:0] output_frame;
    logic [3:0] output_index;
    logic signed [7:0] output_bfp;
    logic [11:0] error_detail;
    mfcc_dma_transport #(
        .CORE_ID(CORE_KIND == 1 ? 32'd1 : 32'd2),
        .FORMAT(CORE_KIND == 1 ? 32'h00011828 : 32'h00030020),
        .CONTRACT_TAG(CORE_KIND == 1 ? 32'h283fff8a : 32'hc556a8e8)
    ) U_TRANSPORT (
        .s_axi_aclk(s_axi_aclk), .s_axi_aresetn(s_axi_aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awprot(s_axi_awprot),
        .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arprot(s_axi_arprot),
        .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp),
        .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready),
        .s_axis_pcm_tdata(s_axis_pcm_tdata), .s_axis_pcm_tkeep(s_axis_pcm_tkeep),
        .s_axis_pcm_tlast(s_axis_pcm_tlast), .s_axis_pcm_tvalid(s_axis_pcm_tvalid), .s_axis_pcm_tready(s_axis_pcm_tready),
        .m_axis_result_tdata(m_axis_result_tdata), .m_axis_result_tkeep(m_axis_result_tkeep),
        .m_axis_result_tlast(m_axis_result_tlast), .m_axis_result_tvalid(m_axis_result_tvalid), .m_axis_result_tready(m_axis_result_tready),
        .irq(irq), .o_clip_start(clip_start), .o_abort(abort_core),
        .o_pcmvalid(pcm_valid), .i_pcmready(pcm_ready), .o_pcm(pcm), .o_pcm_last(pcm_last),
        .i_outputvalid(output_valid), .o_outputready(output_ready), .i_data64(output_payload),
        .i_frame32(output_frame), .i_index4(output_index), .i_bfp8(output_bfp),
        .i_last(output_last), .i_error(output_error), .i_metadata_error(metadata_error),
        .i_core_error_detail(error_detail), .i_clipdone(clip_done)
    );
    generate
        if (CORE_KIND == 1) begin : G_FIXED
    logic signed [39:0] output_value;
    assign core_rst_n = s_axi_aresetn && !abort_core;
    assign output_payload = {{24{output_value[39]}},output_value};
    assign metadata_error = 1'b0;
    assign error_detail = {11'd0,output_error};
    mfcc_fixed_top #(.MEL_INIT_FILE(MEL_INIT_FILE)) U_CORE (
        .clk(s_axi_aclk), .rst_n(core_rst_n), .i_clip_start(clip_start),
        .i_pcm_valid(pcm_valid), .o_pcm_ready(pcm_ready), .i_pcm(pcm), .i_pcm_last(pcm_last),
        .o_valid(output_valid), .i_ready(output_ready), .o_mfcc(output_value),
        .o_frame(output_frame), .o_index(output_index), .o_bfp_s(output_bfp),
        .o_last(output_last), .o_error(output_error), .o_clip_done(clip_done),
        // Internal debug streams remain available through hierarchy in TBs;
        // they are deliberately absent from the PS/PL production interface.
        .o_front_valid(), .o_front_ready(), .o_front_q(), .o_front_frame(), .o_front_index(), .o_front_s(),
        .o_fft_valid(), .o_fft_re(), .o_fft_im(), .o_fft_bin(), .o_fft_last(),
        .o_mel_valid(), .o_mel_ready(), .o_mel_value(), .o_mel_band(), .o_mel_frame(), .o_mel_s()
    );
        end else if (CORE_KIND == 2) begin : G_FP32
    logic [11:0] core_error;
    logic start_valid, start_ready, sample_valid, sample_ready, end_valid, end_ready;
    logic [15:0] sample_pcm;
    logic result_valid, result_ready, result_last, done_valid, done_ready;
    logic [31:0] result_data, result_frame, result_start;
    logic [3:0] result_index;

    fp32_core_adapter U_ADAPTER (
        .clk(s_axi_aclk), .rst_n(s_axi_aresetn), .i_clip_start(clip_start), .i_abort(abort_core),
        .i_pcmvalid(pcm_valid), .o_pcmready(pcm_ready), .i_pcm(pcm), .i_pcm_last(pcm_last),
        .o_outputvalid(output_valid), .i_outputready(output_ready), .o_data64(output_payload),
        .o_frame32(output_frame), .o_index4(output_index), .o_bfp8(output_bfp),
        .o_last(output_last), .o_error(output_error), .o_metadata_error(metadata_error),
        .o_core_error_detail(error_detail), .o_clipdone(clip_done), .o_core_rst_n(core_rst_n),
        .o_start_valid(start_valid), .i_start_ready(start_ready),
        .o_sample_valid(sample_valid), .i_sample_ready(sample_ready), .o_sample_pcm(sample_pcm),
        .o_end_valid(end_valid), .i_end_ready(end_ready),
        .i_result_valid(result_valid), .o_result_ready(result_ready), .i_result_data(result_data),
        .i_result_index(result_index), .i_result_frame(result_frame), .i_result_start(result_start),
        .i_result_last(result_last), .i_done_valid(done_valid), .o_done_ready(done_ready),
        .i_core_error(core_error)
    );
    fp32_mfcc U_CORE (
        .clk(s_axi_aclk), .rst_n(core_rst_n),
        .clip_start_valid(start_valid), .clip_start_ready(start_ready),
        .s_valid(sample_valid), .s_ready(sample_ready), .s_pcm(sample_pcm),
        .clip_end_valid(end_valid), .clip_end_ready(end_ready),
        .m_valid(result_valid), .m_ready(result_ready), .m_data(result_data),
        .m_coeff_index(result_index), .m_frame_id(result_frame), .m_start_sample(result_start), .m_last(result_last),
        .clip_done_valid(done_valid), .clip_done_ready(done_ready),
        .o_error(core_error), .o_busy() // Native busy is superseded by transport start-to-done accounting.
    );
        end else begin : G_INVALID
            assign core_rst_n = 1'b0;
            assign pcm_ready = 1'b0;
            assign output_valid = 1'b0;
            assign output_payload = 64'd0;
            assign output_frame = 32'd0;
            assign output_index = 4'd0;
            assign output_bfp = 8'sd0;
            assign output_last = 1'b0;
            assign output_error = 1'b1;
            assign metadata_error = 1'b0;
            assign error_detail = 12'd1;
            assign clip_done = 1'b0;
        end
    endgenerate
endmodule
`default_nettype wire
