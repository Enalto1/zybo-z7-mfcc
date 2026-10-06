set source_root [file normalize [lindex $argv 0]]
set run_root [file normalize [lindex $argv 1]]
if {[version -short] ne "2024.2"} {error "Vivado2024.2 required"}
set_param general.maxThreads 2
create_project fixed_spectral_ooc $run_root/project -part xc7z020clg400-1
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
add_files [glob $source_root/hardware/fixed/fft/*.sv]
add_files $source_root/hardware/fixed/power_mel/fixed_spectral_tail.sv
add_files $source_root/hardware/fixed/fixed_spectral_top.sv
add_files $run_root/twiddle_1024_w16.mem
add_files $run_root/mel_fw16.mem
set_property top fixed_spectral_top [current_fileset]
update_compile_order -fileset sources_1
check_syntax
set fh [open $run_root/clock.xdc w]
puts $fh {create_clock -name clk -period 10.000 [get_ports clk]}
puts $fh {set_input_delay 1.000 -clock clk [get_ports -filter {DIRECTION == IN && NAME != clk}]}
puts $fh {set_output_delay 1.000 -clock clk [get_ports -filter {DIRECTION == OUT}]}
close $fh
read_xdc $run_root/clock.xdc
synth_design -top fixed_spectral_top -part xc7z020clg400-1 -mode out_of_context
write_checkpoint -force $run_root/post_synth.dcp
report_utilization -hierarchical -file $run_root/synth_utilization.rpt
report_timing_summary -file $run_root/synth_timing.rpt
set fh [open $run_root/bram_cells.txt w]
foreach cell [get_cells -hier -filter {REF_NAME =~ RAMB*}] {puts $fh "$cell [get_property REF_NAME $cell]"}
close $fh
opt_design
place_design
phys_opt_design
route_design
write_checkpoint -force $run_root/post_route.dcp
report_utilization -hierarchical -file $run_root/route_utilization.rpt
report_timing_summary -report_unconstrained -file $run_root/route_timing.rpt
report_drc -file $run_root/route_drc.rpt
set fh [open $run_root/implementation_status.txt w]
puts $fh "post_route_complete"
puts $fh "part=xc7z020clg400-1"
puts $fh "clock_period_ns=10"
puts $fh "setup_slack_ns=[get_property SLACK [lindex [get_timing_paths -delay_type max -max_paths 1] 0]]"
puts $fh "hold_slack_ns=[get_property SLACK [lindex [get_timing_paths -delay_type min -max_paths 1] 0]]"
puts $fh "ram_bram_cells=[llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]"
puts $fh "dsp_cells=[llength [get_cells -hier -filter {REF_NAME =~ DSP*}]]"
close $fh
write_project_tcl -force $run_root/recreate_project.tcl
close_project
exit
