`default_nettype none
// Wiring-only fixed adapter. Transport never changes the signed40/F24 result.
// Explicit wire inputs satisfy default_nettype none and Vivado IP packager's
// scalar-port parser; internal and output data retain SystemVerilog logic.
module fixed_accel_top #(
    parameter MEL_INIT_FILE = "mel_fw16.mem"
) (
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
    logic output_valid, output_ready, output_last, output_error, clip_done;
    logic signed [39:0] output_value;
    logic [63:0] output_payload;
    logic [31:0] output_frame;
    logic [3:0] output_index;
    logic signed [7:0] output_bfp;

    assign core_rst_n = s_axi_aresetn && !abort_core;
    assign output_payload = {{24{output_value[39]}}, output_value};

    mfcc_mmio #(
        .CORE_ID(32'd1), .FORMAT(32'h00011828), .CONTRACT_TAG(32'h283fff8a)
    ) U_MMIO (
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
        .i_last(output_last), .i_error(output_error), .i_clipdone(clip_done)
    );
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
endmodule
`default_nettype wire
