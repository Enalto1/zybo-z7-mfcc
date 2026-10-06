if {$argc != 2} {error "Expected run_dir ip_run"}
set run_dir [file normalize [lindex $argv 0]]
set ip_run [file normalize [lindex $argv 1]]
if {[version -short] ne "2024.2"} {error "Vivado 2024.2 required"}
create_project fp32_alu_unit [file join $run_dir project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
foreach ip {fp32_mul fp32_addsub fp32_log} {
    read_ip [file join $ip_run project fp32_ips.srcs sources_1 ip $ip ${ip}.xci]
}
add_files [file join $run_dir source hardware fp32 rtl fp32_alu.sv]
add_files -fileset sim_1 [file join $run_dir source verification fp32 f1_alu tb_fp32_alu_unit.sv]
set_property top fp32_alu [get_filesets sources_1]
set_property top tb_fp32_alu_unit [get_filesets sim_1]
set_property xsim.elaborate.debug_level typical [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set_property -dict [list xsim.simulate.xsim.more_options [list -testplusarg "OUTPUT=$run_dir"]] [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
report_ip_status -file [file join $run_dir ip_status.txt]
launch_simulation
close_sim
close_project
exit
