if {$argc != 1} {error "run directory required"}
set run_dir [file normalize [lindex $argv 0]]
file mkdir $run_dir
create_project fp32_ip_probe [file join $run_dir project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set catalog [open [file join $run_dir catalog.txt] w]
puts $catalog [version]
foreach pattern {xilinx.com:ip:floating_point:* xilinx.com:ip:xfft:*} {
    foreach ip [get_ipdefs -all $pattern] {puts $catalog "$ip\t[get_property VLNV $ip]"}
}
close $catalog
create_ip -name floating_point -vendor xilinx.com -library ip -version 7.1 -module_name fp32_probe
create_ip -name xfft -vendor xilinx.com -library ip -version 9.1 -module_name fft_probe
foreach name {fp32_probe fft_probe} {
    set fp [open [file join $run_dir ${name}_properties.tsv] w]
    foreach property [lsort [list_property [get_ips $name]]] {
        puts $fp "$property\t[get_property $property [get_ips $name]]"
    }
    close $fp
}
close_project
exit
