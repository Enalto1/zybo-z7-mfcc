# Minimal PS-only Zybo Z7-20 platform. Never accesses physical hardware.
# Arguments: run_directory official_board_repository_directory
if {$argc != 2} { error "Expected run directory and pinned board repository" }
if {[version -short] ne "2024.2"} { error "This recipe requires Vivado 2024.2" }
set run_dir [file normalize [lindex $argv 0]]
set board_repo [file normalize [lindex $argv 1]]
set design_dir [file join $run_dir design]
file mkdir $design_dir
set report_dir [file join $run_dir reports]
file mkdir $report_dir

set help_file [open [file join $report_dir write_hw_platform_help.txt] w]
puts $help_file [help write_hw_platform]
close $help_file
set_param board.repoPaths [list $board_repo]
set board_id "digilentinc.com:zybo-z7-20:part0:1.2"
if {[llength [get_board_parts -quiet $board_id]] != 1} { error "Pinned Zybo Z7-20 board definition was not found" }
if {[llength [get_parts -quiet xc7z020clg400-1]] != 1} { error "Target device is not installed" }
create_project zybo_z7_20_ps [file join $design_dir vivado_project] -part xc7z020clg400-1
set_property board_part $board_id [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
create_bd_design zybo_z7_20_ps
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1"} $ps

# Keep official DDR, MIO, UART and PS clocks. Remove all unused PS/PL fabric ports.
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {0} CONFIG.PCW_USE_M_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_GP0 {0} CONFIG.PCW_USE_S_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP0 {0} CONFIG.PCW_USE_S_AXI_HP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP2 {0} CONFIG.PCW_USE_S_AXI_HP3 {0} \
    CONFIG.PCW_USE_S_AXI_ACP {0} \
    CONFIG.PCW_EN_CLK0_PORT {0} CONFIG.PCW_EN_CLK1_PORT {0} \
    CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} \
    CONFIG.PCW_EN_RST0_PORT {0} CONFIG.PCW_EN_RST1_PORT {0} \
    CONFIG.PCW_EN_RST2_PORT {0} CONFIG.PCW_EN_RST3_PORT {0}] $ps

validate_bd_design
save_bd_design
if {[llength [get_bd_cells]] != 1} { error "Unexpected non-PS processing block" }
set interface_names [lsort [lmap port [get_bd_intf_ports] {string trimleft $port /}]]
if {$interface_names ne {DDR FIXED_IO}} { error "Unexpected external interfaces: $interface_names" }
set parameter_file [open [file join $report_dir ps7_parameters.tsv] w]
puts $parameter_file "property\tvalue"
foreach property [lsort [list_property $ps]] {
    puts $parameter_file "$property\t[get_property $property $ps]"
}
close $parameter_file
report_property -all $ps -file [file join $report_dir ps7_properties.txt]
write_bd_tcl [file join $design_dir zybo_z7_20_ps_recreate.tcl]
set bd_file [get_files */zybo_z7_20_ps.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
set_property top zybo_z7_20_ps_wrapper [current_fileset]
update_compile_order -fileset sources_1
write_hw_platform -fixed -force -file [file join $design_dir zybo_z7_20_ps.xsa]
set summary [open [file join $report_dir platform_summary.txt] w]
puts $summary "VIVADO=[version -short]"
puts $summary "PART=[get_property PART [current_project]]"
puts $summary "BOARD_PART=[get_property BOARD_PART [current_project]]"
puts $summary "BD_CELLS=[get_bd_cells]"
puts $summary "EXTERNAL_INTERFACES=$interface_names"
puts $summary "VALIDATE_BD_DESIGN=passed"
puts $summary "BITSTREAM_INCLUDED=false"
puts $summary "PHYSICAL_BOARD_ACCESSED=false"
foreach property {CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ CONFIG.PCW_CPU_CPU_PLL_FREQMHZ CONFIG.PCW_CPU_PERIPHERAL_FREQMHZ CONFIG.PCW_ACT_APU_PERIPHERAL_FREQMHZ CONFIG.PCW_DDR_DDR_PLL_FREQMHZ CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ CONFIG.PCW_UIPARAM_DDR_PARTNO CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH CONFIG.PCW_DDR_RAM_HIGHADDR CONFIG.PCW_UART1_PERIPHERAL_ENABLE CONFIG.PCW_UART1_UART1_IO CONFIG.PCW_UART1_BAUD_RATE CONFIG.PCW_UART_PERIPHERAL_FREQMHZ CONFIG.PCW_USE_M_AXI_GP0} {
    puts $summary "$property=[get_property $property $ps]"
}
close $summary
close_project
puts "ARM_PLATFORM_XSA=[file join $design_dir zybo_z7_20_ps.xsa]"
exit
