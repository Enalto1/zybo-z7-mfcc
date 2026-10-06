`default_nettype none
// Wiring-only FP32 system wrapper. Native raw IEEE754 bits are zero-extended64.
// Explicit wire inputs satisfy default_nettype none and Vivado IP packager's
// scalar-port parser; internal and output data retain SystemVerilog logic.
module fp32_accel_top (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 100000000" *)
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
    input wire s_axi_rready
);
    logic clip_start, abort_core, core_rst_n;
    logic pcm_valid, pcm_ready, pcm_last;
    logic signed [15:0] pcm;
    logic output_valid, output_ready, output_last, output_error, metadata_error, clip_done;
    logic [63:0] output_payload;
    logic [31:0] output_frame;
    logic [3:0] output_index;
    logic signed [7:0] output_bfp;
    logic [11:0] core_error, error_detail;
    logic start_valid, start_ready, sample_valid, sample_ready, end_valid, end_ready;
    logic [15:0] sample_pcm;
    logic result_valid, result_ready, result_last, done_valid, done_ready;
    logic [31:0] result_data, result_frame, result_start;
    logic [3:0] result_index;

    fp32_mmio U_MMIO (
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
        .o_clip_start(clip_start), .o_abort(abort_core),
        .o_pcmvalid(pcm_valid), .i_pcmready(pcm_ready), .o_pcm(pcm), .o_pcm_last(pcm_last),
        .i_outputvalid(output_valid), .o_outputready(output_ready), .i_data64(output_payload),
        .i_frame32(output_frame), .i_index4(output_index), .i_bfp8(output_bfp),
        .i_last(output_last), .i_error(output_error), .i_metadata_error(metadata_error),
        .i_core_error_detail(error_detail), .i_clipdone(clip_done)
    );
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
endmodule
`default_nettype wire
