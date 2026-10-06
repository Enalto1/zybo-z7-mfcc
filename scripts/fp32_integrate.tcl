# Run from a new directory. All authored sources are frozen in run/source first.
if {$argc != 3} {error "Expected run_dir ip_run sim|synth|all"}
set run_dir [file normalize [lindex $argv 0]]
set ip_run [file normalize [lindex $argv 1]]
set mode [lindex $argv 2]
if {[version -short] ne "2024.2"} {error "Vivado 2024.2 required"}
create_project fp32_mfcc [file join $run_dir project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
foreach ip {fp32_mul fp32_addsub fp32_log fp32_pcm16 fp32_fft512} {
    import_ip -files [file join $ip_run project fp32_ips.srcs sources_1 ip $ip ${ip}.xci]
}
generate_target all [get_ips]
add_files [glob [file join $run_dir source hardware fp32 rtl *.sv]]
add_files [glob [file join $run_dir coefficients *.mem]]
set_property top fp32_mfcc [get_filesets sources_1]
set constraint [open [file join $run_dir clock.xdc] w]
puts $constraint {create_clock -name clk -period 10.000 [get_ports clk]}
puts $constraint {set_property HD.CLK_SRC BUFGCTRL_X0Y0 [get_ports clk]}
close $constraint
add_files -fileset constrs_1 [file join $run_dir clock.xdc]
add_files -fileset sim_1 [file join $run_dir source verification fp32 tb_fp32_mfcc.sv]
set_property include_dirs [list [file join $run_dir source verification fp32]] [get_filesets sim_1]
set_property top tb_fp32_mfcc [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property -name xsim.elaborate.xelab.more_options -value {--O3} -objects [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set sim_args [list -testplusarg "CASES=[file join $run_dir cases.txt]" -testplusarg "OUTPUT=$run_dir"]
if {[file exists [file join $run_dir reset_stress_input.txt]]} {
    set file_handle [open [file join $run_dir reset_stress_input.txt] r]
    set reset_input [string trim [read $file_handle]]
    close $file_handle
    lappend sim_args -testplusarg RESET_STRESS -testplusarg "RESET_PCM=$reset_input"
}
set_property -name xsim.simulate.xsim.more_options -value $sim_args -objects [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
report_ip_status -file [file join $run_dir ip_status.txt]
if {$mode in {sim all}} {
    launch_simulation
    close_sim
}
if {$mode in {synth all}} {
    set_property -name {STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS} -value {-mode out_of_context} -objects [get_runs synth_1]
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
    open_run synth_1
    if {[llength [get_sites BUFGCTRL_X0Y0]] != 1} {error "OOC clock source assumption unavailable"}
    report_utilization -hierarchical -file [file join $run_dir utilization_hierarchical.txt]
    report_utilization -file [file join $run_dir utilization.txt]
    report_timing_summary -file [file join $run_dir timing_synth.txt]
    report_drc -file [file join $run_dir drc_synth.txt]
    write_checkpoint -force [file join $run_dir fp32_mfcc_synth.dcp]
    opt_design
    place_design
    phys_opt_design
    route_design
    report_timing_summary -report_unconstrained -file [file join $run_dir timing_routed.txt]
    report_utilization -file [file join $run_dir utilization_routed.txt]
    report_drc -file [file join $run_dir drc_routed.txt]
    write_checkpoint -force [file join $run_dir fp32_mfcc_routed.dcp]
}
close_project
exit
