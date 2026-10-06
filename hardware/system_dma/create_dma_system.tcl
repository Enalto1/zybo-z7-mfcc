if {[catch {
# ZYBO Z7-20 PS7 + custom DMA MFCC AXI4-Lite system. No hardware access.
# Arguments: fresh_run_directory pinned_board_repository {fixed|fp32}
# The caller snapshots sources beneath RUN/source before invoking this script.
if {$argc != 3} { error "Expected run_dir board_repo variant" }
if {[version -short] ne "2024.2"} { error "Vivado 2024.2 is required" }
set run_dir [file normalize [lindex $argv 0]]
if {[string length $run_dir]>40} { error "Use a run directory of at most 40 characters: Windows IP checkpoint paths are limited to 260 bytes" }
set board_repo [file normalize [lindex $argv 1]]
set variant [lindex $argv 2]
if {$variant ni {fixed fp32}} { error "Unknown arithmetic variant" }
set core_kind [expr {$variant eq "fixed" ? 1 : 2}]
set source_dir [file join $run_dir source]
set design_dir [file join $run_dir design]
set report_dir [file join $run_dir reports]
set project_dir [file join $design_dir vivado_project]
set package_project_dir [file join $design_dir ip_pack_project]
set ip_repo_dir [file join $design_dir ip_repo]
set ip_dir [file join $ip_repo_dir mfcc_dma_1_0]
foreach fresh [list $project_dir $package_project_dir $ip_repo_dir] {
    if {[file exists $fresh]} { error "Refusing an existing project/package: $fresh" }
}
if {![file isdirectory $source_dir]} { error "Preserved source snapshot is required" }
foreach required {board.xml preset.xml part0_pins.xml} {
    if {![file isfile [file join $board_repo zybo-z7-20 A.0 $required]]} {
        error "Pinned board repository is incomplete: $required"
    }
}
file mkdir $design_dir
file mkdir $report_dir
set_param general.maxThreads 2
set_param board.repoPaths [list $board_repo]
set board_id "digilentinc.com:zybo-z7-20:part0:1.2"
if {[llength [get_board_parts -quiet $board_id]] != 1} { error "Pinned board definition not found" }
if {[llength [get_parts -quiet xc7z020clg400-1]] != 1} { error "Target device is not installed" }
# Vivado 2024.2 rejects SystemVerilog as a BD module-reference top. Package the
# unchanged SV design as a user IP in a separate project; the final project
# contains only the packaged IP, avoiding duplicate definitions.
create_project mfcc_dma_package $package_project_dir -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]

# The Python freeze enumerates every unchanged arithmetic/transport/ROM input.
set fd [open [file join $run_dir rtl_files.txt] r]
set authored [split [string trim [read $fd]] "\n"]
close $fd
set fd [open [file join $run_dir fixed_mel_init_file.txt] r]
set fixed_mel_init_file [string trim [read $fd]]
close $fd
if {$fixed_mel_init_file ni {mel_fw16.mem mel_sparse_fw16.mem}} { error "Unsupported fixed Mel initialization selection" }
if {$variant eq "fp32" && $fixed_mel_init_file ne "mel_fw16.mem"} { error "FP32 comparison must preserve the baseline fixed ROM parameter" }
set expected_memories [list dct_cosine.mem dct_scale.mem mel.mem mel_compact.mem mel_descriptor.mem mel_fw16.mem twiddle_1024_w16.mem window.mem]
if {$fixed_mel_init_file eq "mel_sparse_fw16.mem"} { lappend expected_memories mel_sparse_fw16.mem }
set expected_memories [lsort $expected_memories]
set authored_memories [list]
foreach file $authored {
    if {[file extension $file] eq ".mem"} { lappend authored_memories [file tail $file] }
}
if {[lsort $authored_memories] ne $expected_memories} { error "Frozen coefficient memory list disagrees with fixed ROM selection" }
if {[llength $authored] < 30} { error "Incomplete frozen arithmetic/transport source list" }
foreach file $authored {
    if {![file isfile $file]} { error "Missing frozen source: $file" }
}
add_files -norecurse $authored
# Package XCI customizations, not generated vendor HDL or simulation stubs.
# UG1118: parent regeneration creates each nested customization's products.
foreach ip {fp32_mul fp32_addsub fp32_log fp32_pcm16 fp32_fft512} {
    import_ip -files [file join $run_dir ip $ip ${ip}.xci]
}
report_ip_status -file [file join $report_dir imported_ip_status.txt]
foreach ip [get_ips] {
    if {[get_property IS_LOCKED $ip]} { error "Locked frozen IP: $ip" }
}
set_property file_type {Memory Initialization Files} [get_files *.mem]
set_property top mfcc_dma_top [get_filesets sources_1]
update_compile_order -fileset sources_1
set originals $authored
set source_by_basename [dict create]
foreach original $originals {
    set basename [file tail $original]
    if {[dict exists $source_by_basename $basename]} { error "Duplicate package source basename: $basename" }
    dict set source_by_basename $basename [file normalize $original]
}
ipx::package_project -root_dir $ip_dir -vendor mfcc.local -library user \
    -taxonomy /UserIP -import_files -set_current true
set core [ipx::current_core]
set_property -dict [list name mfcc_dma version 1.0 display_name {DMA MFCC AXI4-Lite} \
    description {Unchanged fixed/FP32 arithmetic with common DMA transport ABI 2.0} \
    supported_families {zynq Production} xpm_libraries {XPM_MEMORY}] $core
set core_axi [ipx::get_bus_interfaces S_AXI -of_objects $core]
if {[llength $core_axi] != 1} { error "Packager did not infer the annotated S_AXI interface" }
foreach pair {{s_axi_aclk xilinx.com:signal:clock_rtl:1.0} {s_axi_aresetn xilinx.com:signal:reset_rtl:1.0}} {
    lassign $pair interface abstraction
    if {[llength [ipx::get_bus_interfaces $interface -of_objects $core]] == 0} {
        ipx::infer_bus_interface $interface $abstraction $core
    }
}
foreach interface {S_AXI S_AXIS_PCM M_AXIS_RESULT} {
    if {[llength [ipx::get_bus_interfaces $interface -of_objects $core]] != 1} {
        error "Missing annotated transport interface: $interface"
    }
    ipx::associate_bus_interfaces -busif $interface -clock s_axi_aclk $core
}
ipx::associate_bus_interfaces -clock s_axi_aclk -reset s_axi_aresetn $core
set memory [ipx::get_memory_maps S_AXI -of_objects $core]
if {[llength $memory] == 0} { set memory [ipx::add_memory_map S_AXI $core] }
set_property slave_memory_map_ref S_AXI $core_axi
set blocks [ipx::get_address_blocks -of_objects $memory]
if {[llength $blocks] == 0} { set blocks [ipx::add_address_block Reg $memory] }
if {[llength $blocks] != 1} { error "Expected one IP register block" }
set_property -dict [list base_address 0 range 65536 width 32 usage register] $blocks
ipx::create_xgui_files $core
ipx::update_checksums $core
ipx::check_integrity $core
ipx::save_core $core

# Check authored RTL/ROM bytes in the persistent package. Nested XCI
# configurations are checked independently by the Python runner.
set imported [dict create]
set memory_groups [dict create synthesis 0 simulation 0]
set fd [open [file join $report_dir packaged_files.tsv] w]
puts $fd "file_group\tfile_type\tpackaged_file\tidentical_source"
foreach group [ipx::get_file_groups -of_objects $core] {
    set group_name [get_property NAME $group]
    set group_memories [list]
    foreach member [ipx::get_files -of_objects $group] {
        set relative [get_property NAME $member]
        set basename [file tail $relative]
        if {![dict exists $source_by_basename $basename]} { continue }
        set copied [file normalize [file join $ip_dir $relative]]
        if {![string equal -nocase [string range $copied 0 [string length $ip_dir]] "$ip_dir/"]} {
            error "Packaged source is outside the persistent IP directory: $copied"
        }
        set original [dict get $source_by_basename $basename]
        set a [open $original rb];set expected [read $a];close $a
        set a [open $copied rb];set actual [read $a];close $a
        if {$expected ne $actual} { error "Packaged file changed bytes: $basename" }
        dict set imported $basename $copied
        puts $fd "$group_name\t[get_property TYPE $member]\t$copied\t$original"
        if {[file extension $basename] eq ".mem"} { lappend group_memories $basename }
    }
    set purpose ""
    if {[string match xilinx_anylanguagesynthesis* $group_name]} { set purpose synthesis }
    if {[string match xilinx_anylanguagebehavioralsimulation* $group_name]} { set purpose simulation }
    if {$purpose ne ""} {
        if {[lsort $group_memories] ne $expected_memories} {
            error "The exact frozen coefficient memory set is required in $group_name"
        }
        dict incr memory_groups $purpose
    }
}
close $fd
if {[dict get $memory_groups synthesis] != 1 || [dict get $memory_groups simulation] != 1} {
    error "Expected one synthesis and one simulation IP file group"
}
if {[lsort [dict keys $source_by_basename]] ne [lsort [dict keys $imported]]} {
    error "IP package omitted an RTL/memory source"
}
ipx::unload_core $core
close_project

create_project zybo_dma $project_dir -part xc7z020clg400-1
set_property board_part $board_id [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
set_property ip_repo_paths [list $ip_repo_dir] [current_project]
update_ip_catalog
set accel_vlnv mfcc.local:user:mfcc_dma:1.0
if {[llength [get_ipdefs -quiet $accel_vlnv]] != 1} { error "Packaged DMA accelerator not found" }

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
set fabric [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $fabric
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_fclk0_100m]
set_property -dict [list CONFIG.C_AUX_RESET_HIGH {1} \
    CONFIG.C_EXT_RST_WIDTH {4} CONFIG.C_AUX_RST_WIDTH {4}] $reset
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 constant_zero]
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 constant_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
set accel [create_bd_cell -type ip -vlnv $accel_vlnv mfcc_dma_0]
set_property CONFIG.CORE_KIND $core_kind $accel
set_property CONFIG.MEL_INIT_FILE $fixed_mel_init_file $accel
if {[get_property CONFIG.MEL_INIT_FILE $accel] ne $fixed_mel_init_file} { error "Fixed Mel ROM parameter was not applied" }
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {1} \
    CONFIG.c_addr_width {32} CONFIG.c_sg_length_width {23} \
    CONFIG.c_m_axi_mm2s_data_width {64} CONFIG.c_m_axi_s2mm_data_width {64} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_mm2s_burst_size {16} CONFIG.c_s2mm_burst_size {16} \
    CONFIG.c_include_mm2s_dre {1} CONFIG.c_include_s2mm_dre {1} \
    CONFIG.c_include_mm2s_sf {1} CONFIG.c_include_s2mm_sf {1}] $dma
set hp [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 hp_interconnect_0]
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $hp
set irq [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
set_property -dict [list CONFIG.NUM_PORTS {4} CONFIG.IN0_WIDTH {1} CONFIG.IN1_WIDTH {1} CONFIG.IN2_WIDTH {1} CONFIG.IN3_WIDTH {13}] $irq
set irqzero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 irq_zero]
set_property -dict [list CONFIG.CONST_WIDTH {13} CONFIG.CONST_VAL {0}] $irqzero
set axislave [get_bd_intf_pins -of_objects $accel -filter {MODE == Slave && VLNV == xilinx.com:interface:aximm_rtl:1.0}]
if {[llength $axislave] != 1} { error "mfcc_dma_top must expose exactly one AXI slave" }
if {[get_property CONFIG.PROTOCOL $axislave] ne "AXI4LITE" ||
    [get_property CONFIG.ADDR_WIDTH $axislave] != 16 ||
    [get_property CONFIG.DATA_WIDTH $axislave] != 32} {
    error "Accelerator interface metadata must declare AXI4LITE, ADDR_WIDTH16, DATA_WIDTH32"
}

connect_bd_intf_net [get_bd_intf_pins $ps/M_AXI_GP0] [get_bd_intf_pins $fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $fabric/M00_AXI] $axislave
connect_bd_intf_net [get_bd_intf_pins $fabric/M01_AXI] [get_bd_intf_pins $dma/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_MM2S] [get_bd_intf_pins $hp/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_S2MM] [get_bd_intf_pins $hp/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins $hp/M00_AXI] [get_bd_intf_pins $ps/S_AXI_HP0]
connect_bd_intf_net [get_bd_intf_pins $dma/M_AXIS_MM2S] [get_bd_intf_pins $accel/S_AXIS_PCM]
connect_bd_intf_net [get_bd_intf_pins $accel/M_AXIS_RESULT] [get_bd_intf_pins $dma/S_AXIS_S2MM]
connect_bd_net [get_bd_pins $ps/FCLK_CLK0] \
    [get_bd_pins $ps/M_AXI_GP0_ACLK] [get_bd_pins $ps/S_AXI_HP0_ACLK] \
    [get_bd_pins $fabric/ACLK] [get_bd_pins $fabric/S00_ACLK] [get_bd_pins $fabric/M00_ACLK] [get_bd_pins $fabric/M01_ACLK] \
    [get_bd_pins $hp/ACLK] [get_bd_pins $hp/S00_ACLK] [get_bd_pins $hp/S01_ACLK] [get_bd_pins $hp/M00_ACLK] \
    [get_bd_pins $dma/s_axi_lite_aclk] [get_bd_pins $dma/m_axi_mm2s_aclk] [get_bd_pins $dma/m_axi_s2mm_aclk] \
    [get_bd_pins $reset/slowest_sync_clk] [get_bd_pins $accel/s_axi_aclk]
connect_bd_net [get_bd_pins $ps/FCLK_RESET0_N] [get_bd_pins $reset/ext_reset_in]
connect_bd_net [get_bd_pins $zero/dout] [get_bd_pins $reset/aux_reset_in] [get_bd_pins $reset/mb_debug_sys_rst]
connect_bd_net [get_bd_pins $one/dout] [get_bd_pins $reset/dcm_locked]
connect_bd_net [get_bd_pins $reset/interconnect_aresetn] \
    [get_bd_pins $fabric/ARESETN] [get_bd_pins $fabric/S00_ARESETN] [get_bd_pins $fabric/M00_ARESETN] [get_bd_pins $fabric/M01_ARESETN] \
    [get_bd_pins $hp/ARESETN] [get_bd_pins $hp/S00_ARESETN] [get_bd_pins $hp/S01_ARESETN] [get_bd_pins $hp/M00_ARESETN]
connect_bd_net [get_bd_pins $reset/peripheral_aresetn] [get_bd_pins $accel/s_axi_aresetn] [get_bd_pins $dma/axi_resetn]
connect_bd_net [get_bd_pins $dma/mm2s_introut] [get_bd_pins $irq/In0]
connect_bd_net [get_bd_pins $dma/s2mm_introut] [get_bd_pins $irq/In1]
connect_bd_net [get_bd_pins $accel/irq] [get_bd_pins $irq/In2]
connect_bd_net [get_bd_pins $irqzero/dout] [get_bd_pins $irq/In3]
connect_bd_net [get_bd_pins $irq/dout] [get_bd_pins $ps/IRQ_F2P]
set segments [get_bd_addr_segs -of_objects $axislave]
if {[llength $segments] != 1} { error "Expected one accelerator register segment" }
assign_bd_address -offset 0x43C00000 -range 0x00010000 -target_address_space [get_bd_addr_spaces $ps/Data] $segments
set segments [get_bd_addr_segs -of_objects [get_bd_intf_pins $dma/S_AXI_LITE]]
if {[llength $segments] != 1} { error "Expected one DMA control register segment" }
assign_bd_address -offset 0x40400000 -range 0x00010000 -target_address_space [get_bd_addr_spaces $ps/Data] $segments
set ddr [get_bd_addr_segs $ps/S_AXI_HP0/HP0_DDR_LOWOCM]
if {[llength $ddr] != 1} { error "Expected PS HP0 DDR/low-OCM address segment" }
foreach space {Data_MM2S Data_S2MM} {
    assign_bd_address -offset 0x00000000 -range 0x20000000 -target_address_space [get_bd_addr_spaces $dma/$space] $ddr
}
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH $reset] != 0 ||
    [get_property CONFIG.POLARITY [get_bd_pins $reset/ext_reset_in]] ne "ACTIVE_LOW" ||
    [get_property CONFIG.POLARITY [get_bd_pins $reset/peripheral_aresetn]] ne "ACTIVE_LOW"} {
    error "PS active-low reset polarity did not propagate through proc_sys_reset"
}
save_bd_design
set interface_names [lsort [lmap port [get_bd_intf_ports] {string trimleft $port /}]]
if {$interface_names ne {DDR FIXED_IO}} { error "Unexpected external interfaces: $interface_names" }
# get_bd_ports includes the scalar/vector members of DDR and FIXED_IO. Reject
# only ports outside those two PS interfaces, not the interface members.
set all_port_names [lsort [lmap port [get_bd_ports] {string trimleft $port /}]]
set ps_port_names [lsort [lmap port [get_bd_ports -of_objects [get_bd_intf_ports]] {string trimleft $port /}]]
if {$all_port_names ne $ps_port_names} { error "Unexpected external ports outside DDR/FIXED_IO: $all_port_names" }
set fd [open [file join $report_dir external_ports.tsv] w]
puts $fd "interface\tmember_port"
foreach interface [get_bd_intf_ports] {
    foreach port [get_bd_ports -of_objects $interface] { puts $fd "$interface\t$port" }
}
close $fd
set actual_mhz [get_property CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ $ps]
if {![string is double -strict $actual_mhz] || abs($actual_mhz-100.0)>0.01} {
    error "FCLK0 frequency is not 100 MHz: $actual_mhz"
}
set fd [open [file join $report_dir address_map.tsv] w]
puts $fd "address_space\tsegment\toffset\trange"
foreach space [get_bd_addr_spaces] {
    foreach segment [get_bd_addr_segs -of_objects $space] {
        puts $fd "$space\t$segment\t[get_property OFFSET $segment]\t[get_property RANGE $segment]"
    }
}
close $fd
set fd [open [file join $report_dir dma_parameters.tsv] w]
puts $fd "property\tvalue"
foreach property [lsort [list_property $dma]] { puts $fd "$property\t[get_property $property $dma]" }
close $fd
set fd [open [file join $report_dir accelerator_parameters.tsv] w]
puts $fd "property\tvalue"
foreach property [lsort [list_property $accel]] { puts $fd "$property\t[get_property $property $accel]" }
close $fd
set fd [open [file join $report_dir irq_routes.tsv] w]
puts $fd "net\tpins"
foreach pin [list $dma/mm2s_introut $dma/s2mm_introut $accel/irq $irq/dout] {
    set net [get_bd_nets -of_objects [get_bd_pins $pin]]
    if {[llength $net] != 1} { error "IRQ source must have one connected net: $pin" }
    puts $fd "$net\t[lsort [get_bd_pins -of_objects $net]]"
}
close $fd
set fd [open [file join $report_dir ps7_parameters.tsv] w]
puts $fd "property\tvalue"
foreach property [lsort [list_property $ps]] { puts $fd "$property\t[get_property $property $ps]" }
close $fd
report_property -all $ps -file [file join $report_dir ps7_properties.txt]
report_property -all $reset -file [file join $report_dir reset_properties.txt]
report_property -all $axislave -file [file join $report_dir accelerator_axi_properties.txt]
write_bd_tcl [file join $design_dir zybo_dma_recreate.tcl]
set bd_file [get_files */zybo_dma.bd]
generate_target all $bd_file
set wrappers [make_wrapper -files $bd_file -top]
add_files -norecurse $wrappers
set_property top zybo_dma_wrapper [current_fileset]
update_compile_order -fileset sources_1
set fd [open [file join $report_dir project_files.txt] w]
foreach source [lsort [get_files -all]] { puts $fd $source }
close $fd
set fd [open [file join $report_dir platform_summary.txt] w]
puts $fd "VIVADO=[version -short]"
puts $fd "PART=[get_property PART [current_project]]"
puts $fd "BOARD_PART=[get_property BOARD_PART [current_project]]"
puts $fd "EXTERNAL_INTERFACES=$interface_names"
puts $fd "FCLK0_MHZ=$actual_mhz"
puts $fd "ACCELERATOR_BASE=0x43C00000"
puts $fd "ACCELERATOR_RANGE=0x00010000"
puts $fd "ACCELERATOR_VLNV=$accel_vlnv"
puts $fd "ACCELERATOR_IP_DIRECTORY=$ip_dir"
puts $fd "PACKAGED_SOURCE_BYTES=identical"
puts $fd "RESET_EXT_ACTIVE_HIGH=[get_property CONFIG.C_EXT_RESET_HIGH $reset]"
puts $fd "INTERRUPTS=DMA_MM2S:0,DMA_S2MM:1,CORE_TERMINAL:2"
puts $fd "DMA_BASE=0x40400000"
puts $fd "HP0_DATA_WIDTH=64"
puts $fd "CORE_KIND=[get_property CONFIG.CORE_KIND $accel]"
puts $fd "FIXED_MEL_INIT_FILE=[get_property CONFIG.MEL_INIT_FILE $accel]"
puts $fd "VARIANT=$variant"
puts $fd "PHYSICAL_BOARD_ACCESSED=false"
puts $fd "VALIDATE_BD_DESIGN=passed"
close $fd

close_project
puts "DMA_SYSTEM_VARIANT=$variant"
puts "DMA_SYSTEM_PROJECT=[file join $project_dir zybo_dma.xpr]"
puts "DMA_SYSTEM_TCL_SUCCESS"

} fp32_error fp32_options]} {
    puts stderr "DMA_SYSTEM_TCL_FAILURE: $fp32_error"
    puts stderr [dict get $fp32_options -errorinfo]
    exit 1
}
exit 0
