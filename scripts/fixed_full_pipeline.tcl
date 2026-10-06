if {$argc != 3} {error "Expected run_dir implementation simulation_mode"}
set run_dir [file normalize [lindex $argv 0]]
set implementation [lindex $argv 1]
set simulation_mode [lindex $argv 2]
if {[version -short] ne "2024.2"} {error "Vivado 2024.2 required"}
set_param general.maxThreads 2
create_project fixed_full [file join $run_dir project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
add_files [glob [file join $run_dir source hardware fixed fft *.sv]]
add_files [glob [file join $run_dir source hardware fixed frontend *.sv]]
add_files [glob [file join $run_dir source hardware fixed log_dct *.sv]]
add_files [glob [file join $run_dir source hardware fixed power_mel *.sv]]
add_files [file join $run_dir source hardware fixed fixed_spectral_top.sv]
add_files [file join $run_dir source hardware fixed mfcc_fixed_top.sv]
# Project dependencies must point to preserved source files, not disposable
# working-directory copies used by Vivado's memory initialization flow.
add_files [file join $run_dir source hardware fixed power_mel mel_fw16.mem]
add_files [file join $run_dir source hardware fixed power_mel mel_sparse_fw16.mem]
add_files [file join $run_dir source hardware fixed fft twiddle_1024_w16.mem]
set_property file_type {Memory Initialization Files} [get_files *.mem]
add_files -fileset sim_1 [file join $run_dir source verification fixed full_pipeline tb_mfcc_fixed_top.sv]
set_property top mfcc_fixed_top [get_filesets sources_1]
set_property top tb_mfcc_fixed_top [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property -dict [list xsim.simulate.xsim.more_options [list -testplusarg "RUN=$run_dir"]] [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
if {$simulation_mode == "simulate"} {
    launch_simulation
    close_sim
} elseif {$simulation_mode != "reuse"} {
    error "Unknown simulation mode"
}
if {![file exists [file join $run_dir protocol.json]]} {error "Simulation did not finish checks"}
if {$implementation != "none"} {
    read_xdc [file join $run_dir clock.xdc]
    synth_design -top mfcc_fixed_top -part xc7z020clg400-1 -mode out_of_context
    write_checkpoint [file join $run_dir synthesized.dcp]
    report_utilization -hierarchical -file [file join $run_dir utilization_hier_synth.rpt]
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
        report_utilization -hierarchical -file [file join $run_dir utilization_hier_route.rpt]
        set fd [open [file join $run_dir bram_cells_route.txt] w]
        foreach cell [get_cells -hier -filter {REF_NAME =~ RAMB*}] {puts $fd "$cell [get_property REF_NAME $cell]"}
        close $fd
        report_timing_summary -delay_type min_max -report_unconstrained -file [file join $run_dir timing_route.rpt]
        report_drc -file [file join $run_dir drc_route.rpt]
        set fd [open [file join $run_dir timing.json] w]
        set setup_path [get_timing_paths -delay_type max -max_paths 1]
        set hold_path [get_timing_paths -delay_type min -max_paths 1]
        puts $fd "{\"setup_slack_ns\":[get_property SLACK $setup_path],\"hold_slack_ns\":[get_property SLACK $hold_path]}"
        close $fd
    }
}
close_project
exit
