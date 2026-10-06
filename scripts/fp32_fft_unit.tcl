if {$argc!=3} {error "usage: fp32_fft_unit.tcl project_root output_dir source_xci"}
set project_root [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set source_xci [file normalize [lindex $argv 2]]
create_project fft_unit [file join $output_dir vivado_project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
# Import just this IP into the new test project; never regenerate the pinned IP run.
import_ip -files $source_xci
generate_target simulation [get_ips fp32_fft512]
report_property [get_ips fp32_fft512] -file [file join $output_dir fft_ip_properties.rpt]
add_files -fileset sim_1 -norecurse [file join $project_root verification fp32 f1_fft tb_fp32_fft_unit.sv]
add_files -fileset sim_1 -norecurse [list [file join $output_dir inputs.mem] [file join $output_dir expected_real.mem] [file join $output_dir expected_imag.mem]]
set_property top tb_fp32_fft_unit [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation -simset sim_1 -mode behavioral
close_sim
close_project
