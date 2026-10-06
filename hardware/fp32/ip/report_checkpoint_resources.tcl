# Read-only comparison of explicitly supplied synthesized/routed checkpoints.
if {$argc < 3 || ($argc % 2) != 1} {error "output_directory label checkpoint ?label checkpoint...?"}
set report_dir [file normalize [lindex $argv 0]]
file mkdir $report_dir
set provenance [open [file join $report_dir checkpoints.tsv] w]
puts $provenance "label\tcheckpoint\tpart\tdsp_count"
foreach {label checkpoint} [lrange $argv 1 end] {
    if {![regexp {^[A-Za-z0-9_]+$} $label]} {error "Invalid report label"}
    open_checkpoint $checkpoint
    report_utilization -hierarchical -file [file join $report_dir ${label}_hierarchical.txt]
    set dsp_cells [lsort [get_cells -hierarchical -filter {REF_NAME == DSP48E1}]]
    set cells_file [open [file join $report_dir ${label}_dsp_cells.txt] w]
    foreach cell $dsp_cells {puts $cells_file $cell}
    close $cells_file
    puts $provenance "$label\t[file normalize $checkpoint]\t[get_property PART [current_project]]\t[llength $dsp_cells]"
    close_project
}
close $provenance
exit
