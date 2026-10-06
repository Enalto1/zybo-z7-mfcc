if {$argc != 3} {error "Expected run_dir frames implementation"}
set run_dir [file normalize [lindex $argv 0]]
set frames [lindex $argv 1]
set implementation [lindex $argv 2]
if {[version -short] ne "2024.2"} {error "Vivado 2024.2 required"}
create_project fixed_power_mel [file join $run_dir project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
add_files [file join $run_dir source hardware fixed power_mel fixed_spectral_tail.sv]
add_files [file join $run_dir source hardware fixed power_mel fixed_mel_descriptor.sv]
add_files [file join $run_dir source hardware fixed power_mel mel_sparse_fw16.mem]
add_files [file join $run_dir mel_fw16.mem]
set_property file_type {Memory Initialization Files} [get_files *.mem]
add_files -fileset sim_1 [file join $run_dir source verification fixed power_mel tb_fixed_spectral_tail.sv]
set_property top fixed_spectral_tail [get_filesets sources_1]
set_property top tb_fixed_spectral_tail [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property -dict [list xsim.simulate.xsim.more_options [list -testplusarg "RUN=$run_dir" -testplusarg "FRAMES=$frames"]] [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
launch_simulation
close_sim
if {![file exists [file join $run_dir protocol.json]]} {error "Simulation did not finish checks"}
if {$implementation != "none"} {
    read_xdc [file join $run_dir clock.xdc]
    synth_design -top fixed_spectral_tail -part xc7z020clg400-1 -mode out_of_context
    write_checkpoint [file join $run_dir synthesized.dcp]
    report_utilization -file [file join $run_dir utilization_synth.rpt]
    report_timing_summary -file [file join $run_dir timing_synth.rpt]
    set fd [open [file join $run_dir bram_cells.txt] w]
    foreach cell [get_cells -hier -filter {REF_NAME =~ RAMB*}] {puts $fd "$cell [get_property REF_NAME $cell]"}
    close $fd
    if {$implementation == "route"} {
        opt_design
        place_design
        phys_opt_design
        route_design
        write_checkpoint [file join $run_dir routed.dcp]
        report_utilization -file [file join $run_dir utilization_route.rpt]
        report_timing_summary -delay_type min_max -report_unconstrained -file [file join $run_dir timing_route.rpt]
        report_drc -file [file join $run_dir drc_route.rpt]
    }
}
close_project
exit
