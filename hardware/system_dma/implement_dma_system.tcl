# Continue only the frozen packaged/validated project after Python IP/PS audit.
if {[catch {
if {$argc != 2} { error "Expected prepared run directory and variant" }
if {[version -short] ne "2024.2"} { error "Vivado 2024.2 required" }
set run_dir [file normalize [lindex $argv 0]]
set variant [lindex $argv 1]
if {$variant ni {fixed fp32}} { error "Unknown arithmetic variant" }
set design_dir [file join $run_dir design]
set report_dir [file join $run_dir reports]
set project_dir [file join $design_dir vivado_project]
set implementation bitstream
set_param general.maxThreads 2
open_project [file join $project_dir zybo_dma.xpr]
if {$implementation ne "prepare"} {
    launch_runs synth_1 -jobs 2
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%" || [get_property STATUS [get_runs synth_1]] ni {"synth_design Complete!" "synth_design Complete"}} {
        error "Synthesis did not complete: [get_property STATUS [get_runs synth_1]]"
    }
    open_run synth_1
    set fixed_cells [get_cells -hier -filter {REF_NAME == mfcc_fixed_top || ORIG_REF_NAME == mfcc_fixed_top}]
    set fp32_cells [get_cells -hier -filter {REF_NAME == fp32_mfcc || ORIG_REF_NAME == fp32_mfcc}]
    set fd [open [file join $report_dir selected_core_hierarchy.txt] w]
    puts $fd "VARIANT=$variant"
    puts $fd "FIXED_CORE_CELLS=$fixed_cells"
    puts $fd "FP32_CORE_CELLS=$fp32_cells"
    close $fd
    if {$variant eq "fixed" && ([llength $fixed_cells] != 1 || [llength $fp32_cells] != 0)} {
        error "Synthesis did not retain exactly the selected fixed core"
    }
    if {$variant eq "fp32" && ([llength $fp32_cells] != 1 || [llength $fixed_cells] != 0)} {
        error "Synthesis did not retain exactly the selected FP32 core"
    }
    report_utilization -hierarchical -file [file join $report_dir utilization_hier_synth.rpt]
    report_utilization -file [file join $report_dir utilization_synth.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $report_dir timing_synth.rpt]
    write_checkpoint [file join $design_dir synthesized.dcp]
    close_design
}
if {$implementation in {route bitstream}} {
    launch_runs impl_1 -to_step route_design -jobs 2
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "Implementation did not complete: [get_property STATUS [get_runs impl_1]]"
    }
    open_run impl_1
    report_utilization -file [file join $report_dir utilization_route.rpt]
    report_utilization -hierarchical -file [file join $report_dir utilization_hier_route.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $report_dir timing_route.rpt]
    report_clock_utilization -file [file join $report_dir clock_utilization.rpt]
    report_clocks -file [file join $report_dir clocks.rpt]
    report_drc -file [file join $report_dir drc_route.rpt]
    set fd [open [file join $report_dir bram_cells.txt] w]
    foreach cell [get_cells -hier -filter {REF_NAME =~ RAMB*}] { puts $fd "$cell [get_property REF_NAME $cell]" }
    close $fd
    set setup_path [get_timing_paths -delay_type max -max_paths 1]
    set hold_path [get_timing_paths -delay_type min -max_paths 1]
    if {[llength $setup_path] != 1 || [llength $hold_path] != 1} { error "Missing timing paths" }
    set setup_slack [get_property SLACK $setup_path]
    set hold_slack [get_property SLACK $hold_path]
    set fd [open [file join $report_dir timing.json] w]
    puts $fd "{\"setup_slack_ns\":$setup_slack,\"hold_slack_ns\":$hold_slack}"
    close $fd
    write_checkpoint [file join $design_dir routed.dcp]
    if {$setup_slack < 0 || $hold_slack < 0} { error "100 MHz system timing failed; reports preserved" }
    close_design
}
if {$implementation eq "bitstream"} {
    launch_runs impl_1 -to_step write_bitstream -jobs 2
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "Bitstream did not complete: [get_property STATUS [get_runs impl_1]]"
    }
    set bit [file join [get_property DIRECTORY [get_runs impl_1]] zybo_dma_wrapper.bit]
    if {![file isfile $bit]} { error "Missing implementation bitstream" }
    file copy $bit [file join $design_dir mfcc_dma_${variant}.bit]
    open_run impl_1
    write_hw_platform -fixed -include_bit -file [file join $design_dir mfcc_dma_${variant}.xsa]
    close_design
    set fd [open [file join $report_dir platform_summary.txt] a]
    puts $fd "BITSTREAM_INCLUDED=true"
    puts $fd "XSA_EXPORTED=true"
    close $fd
}
close_project
puts "DMA_IMPLEMENTATION_TCL_SUCCESS"
} fp32_error fp32_options]} {
    puts stderr "DMA_IMPLEMENTATION_TCL_FAILURE: $fp32_error"
    puts stderr [dict get $fp32_options -errorinfo]
    exit 1
}
exit 0
