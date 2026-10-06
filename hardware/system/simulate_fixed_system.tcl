# Actual fixed arithmetic behind a transaction-level AXI4-Lite master.
# This bench covers the accelerator boundary; it does not execute an ARM CPU.
if {$argc != 1} { error "Expected fresh run directory" }
if {[version -short] ne "2024.2"} { error "Vivado 2024.2 required" }
set run_dir [file normalize [lindex $argv 0]]
set source_dir [file join $run_dir source]
set_param general.maxThreads 2
create_project fixed_system_sim [file join $run_dir sim_project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
foreach group {fft frontend log_dct} {
    add_files [glob [file join $source_dir hardware fixed $group *.sv]]
}
foreach file {
    hardware/fixed/power_mel/fixed_spectral_tail.sv
    hardware/fixed/fixed_spectral_top.sv
    hardware/fixed/mfcc_fixed_top.sv
    hardware/fixed/power_mel/mel_fw16.mem
    hardware/fixed/fft/twiddle_1024_w16.mem
    hardware/system/mfcc_mmio.sv
    hardware/system/fixed_accel_top.sv
} { add_files [file join $source_dir $file] }
set_property file_type {Memory Initialization Files} [get_files *.mem]
add_files -fileset sim_1 [file join $source_dir verification system tb_mfcc_mmio.sv]
add_files -fileset sim_1 [file join $source_dir verification system tb_fixed_accel.sv]
set_property top fixed_accel_top [get_filesets sources_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property -dict [list xsim.simulate.xsim.more_options [list -testplusarg "RUN=$run_dir"]] [get_filesets sim_1]
foreach top {tb_mfcc_mmio tb_fixed_accel} {
    set_property top $top [get_filesets sim_1]
    update_compile_order -fileset sim_1
    launch_simulation
    close_sim
    # Vivado reuses the simulate.log name between tops; preserve each result.
    set log [file join $run_dir sim_project fixed_system_sim.sim sim_1 behav xsim simulate.log]
    file copy $log [file join $run_dir ${top}_simulate.log]
}
if {![file isfile [file join $run_dir simulation.json]] || ![file isfile [file join $run_dir unit_simulation.json]]} {
    error "Both unit and full accelerator checks must finish"
}
close_project
exit
