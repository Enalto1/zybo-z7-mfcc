if {$argc != 2} {error "run_dir ip_run required"}
if {[version -short] ne "2024.2"} {error "Vivado 2024.2 required"}
set run_dir [file normalize [lindex $argv 0]]
set ip_run [file normalize [lindex $argv 1]]
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
update_compile_order -fileset sources_1
report_ip_status -file [file join $run_dir reports ip_status.txt]
set_property -name {STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS} -value {-mode out_of_context} -objects [get_runs synth_1]
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
open_run synth_1
if {[llength [get_sites BUFGCTRL_X0Y0]] != 1} {error "OOC clock-source site unavailable"}
report_utilization -hierarchical -file [file join $run_dir reports utilization_hierarchical.txt]
report_utilization -file [file join $run_dir reports utilization_synth.txt]
report_timing_summary -file [file join $run_dir reports timing_synth.txt]
report_drc -file [file join $run_dir reports drc_synth.txt]
write_checkpoint -force [file join $run_dir fp32_mfcc_synth.dcp]
opt_design
place_design
phys_opt_design
route_design
report_timing_summary -report_unconstrained -file [file join $run_dir reports timing_routed.txt]
report_utilization -file [file join $run_dir reports utilization_routed.txt]
report_drc -file [file join $run_dir reports drc_routed.txt]
report_route_status -file [file join $run_dir reports route_status.txt]
write_checkpoint -force [file join $run_dir fp32_mfcc_routed.dcp]
set result [open [file join $run_dir reports completion.txt] w]
puts $result [version]
puts $result "TOP=fp32_mfcc"
puts $result "PART=[get_property PART [current_project]]"
puts $result "CLOCK_PERIOD_NS=10.000"
puts $result "OOC_CLOCK_SOURCE_ASSUMPTION=BUFGCTRL_X0Y0"
puts $result "EXTERNAL_IO_DELAYS_CONSTRAINED=false"
puts $result "MODE=out_of_context"
puts $result "ROUTE_COMPLETED=true"
puts $result "BOARD_EXECUTED=false"
puts $result "BITSTREAM_GENERATED=false"
close $result
close_project
exit
