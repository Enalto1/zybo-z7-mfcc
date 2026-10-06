if {[catch {
# ZYBO Z7-20 PS7 + custom FP32 MFCC AXI4-Lite system. No hardware access.
# Arguments: fresh_run_directory pinned_board_repository {prepare|synth|route|bitstream}
# The caller snapshots sources beneath RUN/source before invoking this script.
if {$argc != 3} { error "Expected run_dir board_repo implementation" }
if {[version -short] ne "2024.2"} { error "Vivado 2024.2 is required" }
set run_dir [file normalize [lindex $argv 0]]
if {[string length $run_dir]>40} { error "Use a run directory of at most 40 characters: Windows IP checkpoint paths are limited to 260 bytes" }
set board_repo [file normalize [lindex $argv 1]]
set implementation [lindex $argv 2]
if {$implementation ni {prepare synth route bitstream}} { error "Unknown implementation mode" }
set source_dir [file join $run_dir source]
set design_dir [file join $run_dir design]
set report_dir [file join $run_dir reports]
set project_dir [file join $design_dir vivado_project]
set package_project_dir [file join $design_dir ip_pack_project]
set ip_repo_dir [file join $design_dir ip_repo]
set ip_dir [file join $ip_repo_dir fp32_accel_1_0]
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
create_project fp32_accel_package $package_project_dir -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]

# Read only the frozen six arithmetic RTL files, three transport files and ROMs.
set authored [concat [lsort [glob [file join $source_dir hardware fp32 rtl *.sv]]] \
    [lsort [glob [file join $source_dir hardware system_fp32 *.sv]]] \
    [lsort [glob [file join $run_dir coefficients *.mem]]]]
if {[llength $authored] != 15} { error "Expected six FP32 RTL, three transport RTL and six ROM files" }
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
set_property top fp32_accel_top [get_filesets sources_1]
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
set_property -dict [list name fp32_accel version 1.0 display_name {FP32 MFCC AXI4-Lite} \
    description {Unchanged FP32 MFCC RTL with transport ABI v1} \
    supported_families {zynq Production} xpm_libraries {XPM_MEMORY}] $core
set core_axi [ipx::get_bus_interfaces S_AXI -of_objects $core]
if {[llength $core_axi] != 1} { error "Packager did not infer the annotated S_AXI interface" }
foreach pair {{s_axi_aclk xilinx.com:signal:clock_rtl:1.0} {s_axi_aresetn xilinx.com:signal:reset_rtl:1.0}} {
    lassign $pair interface abstraction
    if {[llength [ipx::get_bus_interfaces $interface -of_objects $core]] == 0} {
        ipx::infer_bus_interface $interface $abstraction $core
    }
}
ipx::associate_bus_interfaces -busif S_AXI -clock s_axi_aclk $core
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
        if {[lsort $group_memories] ne {dct_cosine.mem dct_scale.mem mel.mem mel_compact.mem mel_descriptor.mem window.mem}} {
            error "All six frozen coefficient memories are required in $group_name"
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

create_project zybo_z7_20_fp32 $project_dir -part xc7z020clg400-1
set_property board_part $board_id [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
set_property ip_repo_paths [list $ip_repo_dir] [current_project]
update_ip_catalog
set accel_vlnv mfcc.local:user:fp32_accel:1.0
if {[llength [get_ipdefs -quiet $accel_vlnv]] != 1} { error "Packaged fixed accelerator not found" }

create_bd_design zybo_z7_20_fp32
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1"} $ps
# Preserve the proven official DDR/MIO/APU configuration. Activate only GP0,
# FCLK0 and its reset. Polling has no fabric interrupt or DMA/HP/ACP port.
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_M_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_GP0 {0} CONFIG.PCW_USE_S_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP0 {0} CONFIG.PCW_USE_S_AXI_HP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP2 {0} CONFIG.PCW_USE_S_AXI_HP3 {0} \
    CONFIG.PCW_USE_S_AXI_ACP {0} CONFIG.PCW_USE_FABRIC_INTERRUPT {0} \
    CONFIG.PCW_IRQ_F2P_INTR {0} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_CLK1_PORT {0} \
    CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} \
    CONFIG.PCW_EN_RST0_PORT {1} CONFIG.PCW_EN_RST1_PORT {0} \
    CONFIG.PCW_EN_RST2_PORT {0} CONFIG.PCW_EN_RST3_PORT {0} \
    CONFIG.PCW_FCLK0_PERIPHERAL_CLKSRC {IO PLL} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.000000}] $ps
set fabric [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $fabric
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_fclk0_100m]
set_property -dict [list CONFIG.C_AUX_RESET_HIGH {1} \
    CONFIG.C_EXT_RST_WIDTH {4} CONFIG.C_AUX_RST_WIDTH {4}] $reset
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 constant_zero]
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 constant_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
set accel [create_bd_cell -type ip -vlnv $accel_vlnv fp32_accel_0]
set axislave [get_bd_intf_pins -of_objects $accel -filter {MODE == Slave && VLNV == xilinx.com:interface:aximm_rtl:1.0}]
if {[llength $axislave] != 1} { error "fp32_accel_top must expose exactly one AXI slave" }
if {[get_property CONFIG.PROTOCOL $axislave] ne "AXI4LITE" ||
    [get_property CONFIG.ADDR_WIDTH $axislave] != 16 ||
    [get_property CONFIG.DATA_WIDTH $axislave] != 32} {
    error "Accelerator interface metadata must declare AXI4LITE, ADDR_WIDTH16, DATA_WIDTH32"
}

connect_bd_intf_net [get_bd_intf_pins $ps/M_AXI_GP0] [get_bd_intf_pins $fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $fabric/M00_AXI] $axislave
connect_bd_net [get_bd_pins $ps/FCLK_CLK0] \
    [get_bd_pins $ps/M_AXI_GP0_ACLK] [get_bd_pins $fabric/ACLK] \
    [get_bd_pins $fabric/S00_ACLK] [get_bd_pins $fabric/M00_ACLK] \
    [get_bd_pins $reset/slowest_sync_clk] [get_bd_pins $accel/s_axi_aclk]
# FCLK_RESET0_N is active low; proc_sys_reset performs assertion filtering and
# release synchronization. User RTL consumes peripheral_aresetn synchronously.
connect_bd_net [get_bd_pins $ps/FCLK_RESET0_N] [get_bd_pins $reset/ext_reset_in]
connect_bd_net [get_bd_pins $zero/dout] [get_bd_pins $reset/aux_reset_in] [get_bd_pins $reset/mb_debug_sys_rst]
connect_bd_net [get_bd_pins $one/dout] [get_bd_pins $reset/dcm_locked]
connect_bd_net [get_bd_pins $reset/interconnect_aresetn] [get_bd_pins $fabric/ARESETN] \
    [get_bd_pins $fabric/S00_ARESETN] [get_bd_pins $fabric/M00_ARESETN]
connect_bd_net [get_bd_pins $reset/peripheral_aresetn] [get_bd_pins $accel/s_axi_aresetn]

set segments [get_bd_addr_segs -of_objects $axislave]
if {[llength $segments] != 1} { error "Expected one accelerator register address segment" }
assign_bd_address -offset 0x43C00000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces $ps/Data] $segments
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
set mapped [get_bd_addr_segs -of_objects [get_bd_addr_spaces $ps/Data]]
if {[llength $mapped] != 1} { error "Expected exactly one PS-to-PL memory segment" }
if {[get_property OFFSET $mapped] != 0x43C00000 || [get_property RANGE $mapped] != 0x00010000} {
    error "Accelerator base/range differs from transport ABI"
}
set fd [open [file join $report_dir address_map.tsv] w]
puts $fd "segment\toffset\trange"
puts $fd "$mapped\t[get_property OFFSET $mapped]\t[get_property RANGE $mapped]"
close $fd
set fd [open [file join $report_dir ps7_parameters.tsv] w]
puts $fd "property\tvalue"
foreach property [lsort [list_property $ps]] { puts $fd "$property\t[get_property $property $ps]" }
close $fd
report_property -all $ps -file [file join $report_dir ps7_properties.txt]
report_property -all $reset -file [file join $report_dir reset_properties.txt]
report_property -all $axislave -file [file join $report_dir accelerator_axi_properties.txt]
write_bd_tcl [file join $design_dir zybo_z7_20_fp32_recreate.tcl]
set bd_file [get_files */zybo_z7_20_fp32.bd]
generate_target all $bd_file
set wrappers [make_wrapper -files $bd_file -top]
add_files -norecurse $wrappers
set_property top zybo_z7_20_fp32_wrapper [current_fileset]
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
puts $fd "INTERRUPTS=false"
puts $fd "PHYSICAL_BOARD_ACCESSED=false"
puts $fd "VALIDATE_BD_DESIGN=passed"
close $fd

if {$implementation ne "prepare"} {
    launch_runs synth_1 -jobs 2
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%" || [get_property STATUS [get_runs synth_1]] ni {"synth_design Complete!" "synth_design Complete"}} {
        error "Synthesis did not complete: [get_property STATUS [get_runs synth_1]]"
    }
    open_run synth_1
    report_utilization -hierarchical -file [file join $report_dir utilization_hier_synth.rpt]
    report_utilization -file [file join $report_dir utilization_synth.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $report_dir timing_synth.rpt]
    write_checkpoint [file join $design_dir synthesized.dcp]
    close_design
}
if {$implementation in {route bitstream}} {
    launch_runs impl_1 -to_step route_design -jobs 2
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "Implementation did not complete: [get_property STATUS [get_runs impl_1]]"
    }
    open_run impl_1
    report_utilization -file [file join $report_dir utilization_route.rpt]
    report_utilization -hierarchical -file [file join $report_dir utilization_hier_route.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $report_dir timing_route.rpt]
    report_clock_utilization -file [file join $report_dir clock_utilization.rpt]
    report_clocks -file [file join $report_dir clocks.rpt]
    report_drc -file [file join $report_dir drc_route.rpt]
    set fd [open [file join $report_dir bram_cells.txt] w]
    foreach cell [get_cells -hier -filter {REF_NAME =~ RAMB*}] { puts $fd "$cell [get_property REF_NAME $cell]" }
    close $fd
    set setup_path [get_timing_paths -delay_type max -max_paths 1]
    set hold_path [get_timing_paths -delay_type min -max_paths 1]
    if {[llength $setup_path] != 1 || [llength $hold_path] != 1} { error "Missing timing paths" }
    set setup_slack [get_property SLACK $setup_path]
    set hold_slack [get_property SLACK $hold_path]
    set fd [open [file join $report_dir timing.json] w]
    puts $fd "{\"setup_slack_ns\":$setup_slack,\"hold_slack_ns\":$hold_slack}"
    close $fd
    write_checkpoint [file join $design_dir routed.dcp]
    if {$setup_slack < 0 || $hold_slack < 0} { error "100 MHz system timing failed; reports preserved" }
    close_design
}
if {$implementation eq "bitstream"} {
    launch_runs impl_1 -to_step write_bitstream -jobs 2
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "Bitstream did not complete: [get_property STATUS [get_runs impl_1]]"
    }
    set bit [file join [get_property DIRECTORY [get_runs impl_1]] zybo_z7_20_fp32_wrapper.bit]
    if {![file isfile $bit]} { error "Missing implementation bitstream" }
    file copy $bit [file join $design_dir zybo_z7_20_fp32.bit]
    open_run impl_1
    write_hw_platform -fixed -include_bit -file [file join $design_dir zybo_z7_20_fp32.xsa]
    close_design
    set fd [open [file join $report_dir platform_summary.txt] a]
    puts $fd "BITSTREAM_INCLUDED=true"
    puts $fd "XSA_EXPORTED=true"
    close $fd
}
close_project
puts "FP32_SYSTEM_MODE=$implementation"
puts "FP32_SYSTEM_PROJECT=[file join $project_dir zybo_z7_20_fp32.xpr]"
puts "FP32_SYSTEM_TCL_SUCCESS"

} fp32_error fp32_options]} {
    puts stderr "FP32_SYSTEM_TCL_FAILURE: $fp32_error"
    puts stderr [dict get $fp32_options -errorinfo]
    exit 1
}
exit 0
