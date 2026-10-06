if {[catch {
if {$argc != 2 || [version -short] ne "2024.2"} { error "run_dir board_repo and Vivado2024.2 required" }
set run_dir [file normalize [lindex $argv 0]]
set_param general.maxThreads 2
set_param board.repoPaths [list [file normalize [lindex $argv 1]]]
create_project dma_metadata [file join $run_dir project] -part xc7z020clg400-1
set_property board_part digilentinc.com:zybo-z7-20:part0:1.2 [current_project]
create_bd_design zybo_dma
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1"} $ps
# Preserve the proven official DDR/MIO/APU configuration. Activate GP0, HP0, fabric IRQ and FCLK0 with its reset.
# All three datapaths use the same 100 MHz FCLK0 domain.
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_M_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_GP0 {0} CONFIG.PCW_USE_S_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} CONFIG.PCW_USE_S_AXI_HP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP2 {0} CONFIG.PCW_USE_S_AXI_HP3 {0} \
    CONFIG.PCW_USE_S_AXI_ACP {0} CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_CLK1_PORT {0} \
    CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} \
    CONFIG.PCW_EN_RST0_PORT {1} CONFIG.PCW_EN_RST1_PORT {0} \
    CONFIG.PCW_EN_RST2_PORT {0} CONFIG.PCW_EN_RST3_PORT {0} \
    CONFIG.PCW_FCLK0_PERIPHERAL_CLKSRC {IO PLL} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.000000}] $ps
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {1} \
    CONFIG.c_addr_width {32} CONFIG.c_sg_length_width {23} \
    CONFIG.c_m_axi_mm2s_data_width {64} CONFIG.c_m_axi_s2mm_data_width {64} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_mm2s_burst_size {16} CONFIG.c_s2mm_burst_size {16} \
    CONFIG.c_include_mm2s_dre {1} CONFIG.c_include_s2mm_dre {1} \
    CONFIG.c_include_mm2s_sf {1} CONFIG.c_include_s2mm_sf {1}] $dma
foreach pair [list [list ps7 $ps] [list dma $dma]] {
    lassign $pair name cell
    set fd [open [file join $run_dir ${name}_parameters.tsv] w]
    puts $fd "property\tvalue"
    foreach property [lsort [list_property $cell]] { puts $fd "$property\t[get_property $property $cell]" }
    close $fd
}
save_bd_design
close_project
puts "DMA_METADATA_PROBE_SUCCESS"
} error options]} {
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
