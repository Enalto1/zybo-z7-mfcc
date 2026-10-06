# Exercise the already generated PS7/GP0/interconnect/reset/accelerator design.
# Run after implementation and before the caller records final project hashes.
# This is behavioral PS VIP simulation, not ARM instruction or board execution.
if {$argc != 1} { error "Expected run directory containing the generated system" }
if {[version -short] ne "2024.2"} { error "Vivado 2024.2 required" }
set run_dir [file normalize [lindex $argv 0]]
set project [file join $run_dir design vivado_project zybo_z7_20_fixed.xpr]
set testbench [file join $run_dir source verification system tb_ps_system.sv]
foreach required [list $project $testbench \
    [file join $run_dir ps_pcm.mem] [file join $run_dir ps_mfcc.mem] \
    [file join $run_dir ps_shift.mem]] {
    if {![file isfile $required]} { error "Missing PS simulation dependency: $required" }
}
if {[file exists [file join $run_dir ps_simulation.json]] ||
    [file exists [file join $run_dir tb_ps_system_simulate.log]]} {
    error "Refusing to overwrite an existing PS simulation result"
}
set_param general.maxThreads 2
open_project $project
if {[get_property PART [current_project]] ne "xc7z020clg400-1"} { error "Unexpected project part" }
if {[get_property top [get_filesets sources_1]] ne "zybo_z7_20_fixed_wrapper"} { error "Unexpected generated top" }
add_files -norecurse -fileset sim_1 $testbench
set_property top tb_ps_system [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property -dict [list xsim.simulate.xsim.more_options [list -testplusarg "RUN=$run_dir"]] [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation -mode behavioral
close_sim
set simulation_dir [file join $run_dir design vivado_project zybo_z7_20_fixed.sim sim_1 behav xsim]
foreach name {simulate.log xvlog.log xelab.log} {
    set log [file join $simulation_dir $name]
    if {[file isfile $log]} { file copy $log [file join $run_dir tb_ps_system_$name] }
}
set result [file join $run_dir ps_simulation.json]
if {![file isfile $result]} { error "PS integration test did not produce a completed result" }
set fd [open $result r]
set contents [read $fd]
close $fd
if {![regexp {"status"\s*:\s*"PASS"} $contents]} { error "PS integration test did not pass" }
close_project
puts "PS_SYSTEM_SIMULATION_RESULT=$result"
exit
