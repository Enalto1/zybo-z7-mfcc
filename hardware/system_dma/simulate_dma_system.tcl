# Generated PS7 GP0/HP0/IRQ + real AXI DMA + selected frozen arithmetic.
# Behavioral memory/PS VIP transactions do not execute ARM instructions.
if {[catch {
if {$argc ni {2 3} || [version -short] ne "2024.2"} { error "run_dir variant optional_mode and Vivado2024.2 required" }
set run_dir [file normalize [lindex $argv 0]]
set variant [lindex $argv 1]
set simulation_mode [expr {$argc == 3 ? [lindex $argv 2] : "simulate"}]
if {$simulation_mode ni {prepare simulate}} { error "Unknown simulation preparation mode" }
if {$variant ni {fixed fp32}} { error "Unknown arithmetic variant" }
set core_kind [expr {$variant eq "fixed" ? 1 : 2}]
set project [file join $run_dir design vivado_project zybo_dma.xpr]
set bench [file join $run_dir source verification system_dma tb_dma_system.sv]
foreach file [list $project $bench [file join $run_dir pcm.mem] [file join $run_dir records.mem]] {
    if {![file isfile $file]} { error "Missing DMA simulation dependency: $file" }
}
if {[file exists [file join $run_dir dma_simulation.json]]} { error "Refusing completed simulation overwrite" }
set_param general.maxThreads 2
open_project $project
if {[get_property PART [current_project]] ne "xc7z020clg400-1" || [get_property top [get_filesets sources_1]] ne "zybo_dma_wrapper"} {
    error "Unexpected generated DMA system part/top"
}
add_files -norecurse -fileset sim_1 $bench
# Local, audited vendor model copy: only the four-port DDR arbiter request
# assignments use NBA, after its #0 payload updates. Never a synthesis source.
set vip_dir [file join $run_dir simulation_model patched]
set vip_source [file join $vip_dir processing_system7_vip_v1_0_vl_rfs.sv]
if {![file isfile $vip_source]} { error "Frozen corrected PS VIP is missing" }
add_files -norecurse -fileset sim_1 $vip_source
set vip_file [get_files -of_objects [get_filesets sim_1] $vip_source]
if {[llength $vip_file] != 1} { error "Ambiguous corrected PS VIP source" }
set_property library processing_system7_vip_v1_0_21 $vip_file
set_property used_in_synthesis false $vip_file
set_property used_in_implementation false $vip_file
set_property used_in_simulation true $vip_file
set_property include_dirs [lsort -unique [concat [get_property include_dirs [get_filesets sim_1]] [list $vip_dir]]] [get_filesets sim_1]
set vip_init [file join $run_dir simulation_model all_libraries_xsim.ini]
if {![file isfile $vip_init]} { error "Explicit run-local VIP physical library mapping missing" }
set_property -name xsim.compile.xvlog.more_options -value [list --initfile $vip_init] -objects [get_filesets sim_1]
set_property -name xsim.compile.xvhdl.more_options -value [list --initfile $vip_init] -objects [get_filesets sim_1]
set_property -name xsim.elaborate.xelab.more_options -value [list --initfile $vip_init] -objects [get_filesets sim_1]
if {[llength [get_files -quiet -of_objects [get_filesets sources_1] $vip_source]] != 0} {
    error "Verification model leaked into synthesis fileset"
}
set binding [open [file join $run_dir simulation_model_binding.tsv] w]
puts $binding "path\tlibrary\tused_in_synthesis\tused_in_implementation\tused_in_simulation"
puts $binding "$vip_source\t[get_property library $vip_file]\t[get_property used_in_synthesis $vip_file]\t[get_property used_in_implementation $vip_file]\t[get_property used_in_simulation $vip_file]"
close $binding
set_property top tb_dma_system [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
# Identical compiled i03 sources yielded an X clock with debug off, while a
# debug-all replay exposed one correct VIP clock driver and passed GP0 access.
# Retain visibility for this mixed-language PS/DMA verification; no clock force.
set_property xsim.elaborate.debug_level all [get_filesets sim_1]
set_property -name xsim.simulate.xsim.more_options -value [list -testplusarg "RUN=$run_dir" -testplusarg "CORE_KIND=$core_kind"] -objects [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
launch_simulation -scripts_only -mode behavioral
if {$simulation_mode eq "prepare"} {
    close_project
    puts "DMA_SIMULATION_PREPARE_TCL_SUCCESS"
    exit 0
}
# No compiler may run until every exported PRJ target resolves beneath this
# run. Python populated these libraries from read-only vendor cache clones;
# the independently compiled patched PS VIP library retains its own mapping.
set fd [open $vip_init r]
set mappings [dict create]
foreach line [split [read $fd] "\n"] {
    if {[string trim $line] eq ""} { continue }
    if {![regexp {^([A-Za-z][A-Za-z0-9_]*)=(.+)$} $line -> library physical]} { error "Invalid local library mapping" }
    if {[dict exists $mappings $library]} { error "Duplicate local library mapping" }
    dict set mappings $library [file normalize $physical]
}
close $fd
set ps_library [file join $run_dir simulation_model library]
if {![dict exists $mappings processing_system7_vip_v1_0_21] || [dict get $mappings processing_system7_vip_v1_0_21] ne $ps_library} {
    error "Patched PS VIP physical mapping changed"
}
set simulation_dir [file join $run_dir design vivado_project zybo_dma.sim sim_1 behav xsim]
set compile_targets [list]
foreach name {tb_dma_system_vlog.prj tb_dma_system_vhdl.prj} {
    set fd [open [file join $simulation_dir $name] r]
    foreach line [split [read $fd] "\n"] {
        if {![regexp {^(sv|verilog|vhdl)[ \t]+([^ \t]+)} $line -> language library]} { continue }
        if {![dict exists $mappings $library]} { error "Unmapped compilation target: $library" }
        set physical [dict get $mappings $library]
        if {![string equal -nocase [string range $physical 0 [string length $run_dir]] "$run_dir/"] || ![file isdirectory $physical]} {
            error "Compilation target is not run-local: $library=$physical"
        }
        lappend compile_targets $library
    }
    close $fd
}
if {[llength $compile_targets] == 0} { error "No generated compilation targets" }
set fd [open [file join $run_dir simulation_compile_targets.txt] w]
puts $fd [join [lsort -unique $compile_targets] "\n"]
close $fd
launch_simulation -mode behavioral
close_sim
set simulation_dir [file join $run_dir design vivado_project zybo_dma.sim sim_1 behav xsim]
if {![file isfile [file join $run_dir simulation_model library processing_system7_vip_v1_0_21_arb_wr_4.sdb]]} {
    error "Corrected VIP did not compile to the explicit run-local physical library"
}
foreach name {simulate.log xvlog.log xelab.log} {
    set file [file join $simulation_dir $name]
    if {[file isfile $file]} { file copy $file [file join $run_dir dma_$name] }
}
if {![file isfile [file join $run_dir dma_simulation.json]]} { error "DMA integration simulation did not complete" }
close_project
puts "DMA_SIMULATION_TCL_SUCCESS"
} dma_error dma_options]} {
    puts stderr "DMA_SIMULATION_TCL_FAILURE: $dma_error"
    puts stderr [dict get $dma_options -errorinfo]
    exit 1
}
exit 0
