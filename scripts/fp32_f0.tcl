if {$argc != 2 && $argc != 4} {error "usage: fp32_f0.tcl project_root output_dir ?simulation_only long_clip?"}
set project_root [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set simulation_only 0
set long_clip 0
if {$argc == 4} {
    set simulation_only [lindex $argv 2]
    set long_clip [lindex $argv 3]
}
set rtl [file join $project_root hardware fp32 rtl fp32_frame_buffer.sv]
set tb [file join $project_root verification fp32 f0 tb_fp32_frame_buffer.sv]
create_project f0 [file join $output_dir vivado_project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
add_files -norecurse $rtl
add_files -fileset sim_1 -norecurse $tb
set_property top fp32_frame_buffer [get_filesets sources_1]
set_property top tb_fp32_frame_buffer [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
if {$long_clip} {
    set_property -name xsim.simulate.xsim.more_options -value {-testplusarg F0_LONG_CLIP} -objects [get_filesets sim_1]
}
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
launch_simulation -simset sim_1 -mode behavioral
close_sim
if {$simulation_only} {
    puts "F0_SYNTH_NOT_RUN unchanged_RTL_simulation_only=1"
    close_project
    exit 0
}
synth_design -top fp32_frame_buffer -part xc7z020clg400-1 -mode out_of_context
create_clock -name pl_clk -period 10.000 [get_ports clk]
report_utilization -file [file join $output_dir utilization.rpt]
report_timing_summary -file [file join $output_dir timing_summary.rpt]
report_methodology -file [file join $output_dir methodology.rpt]
set ram18 [llength [get_cells -hier -filter {REF_NAME =~ RAMB18*}]]
set ram36 [llength [get_cells -hier -filter {REF_NAME =~ RAMB36*}]]
set latches [llength [get_cells -hier -filter {REF_NAME =~ LD*}]]
set ff [llength [get_cells -hier -filter {REF_NAME =~ FD*}]]
set lut [llength [get_cells -hier -filter {REF_NAME =~ LUT*}]]
if {$ram18+$ram36 == 0} {error "Frame history did not map to BRAM"}
if {$latches != 0} {error "Unexpected inferred latches"}
set metrics [open [file join $output_dir synthesis_metrics.json] w]
puts $metrics "{\"part\":\"xc7z020clg400-1\",\"ram18\":$ram18,\"ram36\":$ram36,\"latches\":$latches,\"ff\":$ff,\"lut\":$lut}"
close $metrics
write_checkpoint [file join $output_dir fp32_frame_buffer_synth.dcp]
puts "F0_SYNTH_PASS ram18=$ram18 ram36=$ram36 ff=$ff lut=$lut latches=$latches"
close_project
