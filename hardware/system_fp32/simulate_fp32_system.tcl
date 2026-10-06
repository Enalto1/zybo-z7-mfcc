# Actual IP04 FP/FFT/XPM, behind the complete AXI4-Lite adapter and transport.
if {[catch {
if {$argc != 1} { error "Expected frozen run directory" }
if {[version -short] ne "2024.2"} { error "Vivado 2024.2 required" }
set run_dir [file normalize [lindex $argv 0]]
set source_dir [file join $run_dir source]
set_param general.maxThreads 2
create_project fp32_system_sim [file join $run_dir sim_project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
foreach ip {fp32_mul fp32_addsub fp32_log fp32_pcm16 fp32_fft512} {
    import_ip -files [file join $run_dir ip $ip ${ip}.xci]
}
foreach ip [get_ips] {
    if {[get_property IS_LOCKED $ip]} { error "Locked frozen IP: $ip" }
}
generate_target all [get_ips]
add_files [glob [file join $source_dir hardware fp32 rtl *.sv]]
add_files [glob [file join $source_dir hardware system_fp32 *.sv]]
add_files [glob [file join $run_dir coefficients *.mem]]
set_property file_type {Memory Initialization Files} [get_files *.mem]
set_property top fp32_accel_top [get_filesets sources_1]
add_files -fileset sim_1 [file join $source_dir verification system_fp32 tb_fp32_axi_vendor.sv]
set_property top tb_fp32_axi_vendor [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property -name xsim.elaborate.xelab.more_options -value {--O3} -objects [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set_property -name xsim.simulate.xsim.more_options -value [list -testplusarg "RUN=$run_dir"] -objects [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
report_ip_status -file [file join $run_dir simulation_ip_status.txt]
launch_simulation
close_sim
set log [file join $run_dir sim_project fp32_system_sim.sim sim_1 behav xsim simulate.log]
file copy $log [file join $run_dir vendor_axi_simulate.log]
if {![file isfile [file join $run_dir vendor_axi_simulation.json]]} { error "Vendor AXI simulation did not finish" }
close_project
puts "FP32_VENDOR_AXI_TCL_SUCCESS"
} fp32_error fp32_options]} {
    puts stderr "FP32_VENDOR_AXI_TCL_FAILURE: $fp32_error"
    puts stderr [dict get $fp32_options -errorinfo]
    exit 1
}
exit 0
