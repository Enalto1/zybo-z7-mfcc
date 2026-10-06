# Fresh vendor IPs only; authored RTL is integrated by the separate hardware build.
if {$argc != 1} {error "Expected a new run directory"}
if {[version -short] ne "2024.2"} {error "Vivado 2024.2 required"}
set run_dir [file normalize [lindex $argv 0]]
file mkdir [file join $run_dir reports]
create_project fp32_ips [file join $run_dir project] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

foreach {name operation} {fp32_mul Multiply fp32_addsub Add_Subtract fp32_log Logarithm fp32_pcm16 Fixed_to_float} {
    create_ip -name floating_point -vendor xilinx.com -library ip -version 7.1 -module_name $name
    set configuration [list CONFIG.Operation_Type $operation \
        CONFIG.Result_Precision_Type Single \
        CONFIG.Flow_Control Blocking CONFIG.Has_ARESETn true CONFIG.Has_ACLKEN true \
        CONFIG.Has_RESULT_TREADY true CONFIG.Maximum_Latency true \
        CONFIG.C_Rate 1 CONFIG.ACLK_INTF.FREQ_HZ 100000000]
    if {$name eq "fp32_pcm16"} {
        lappend configuration CONFIG.A_Precision_Type Custom CONFIG.C_A_Exponent_Width 1 CONFIG.C_A_Fraction_Width 15
    } else {
        lappend configuration CONFIG.A_Precision_Type Single
    }
    set_property -dict $configuration [get_ips $name]
}
set_property CONFIG.Add_Sub_Value Both [get_ips fp32_addsub]
# Fixed conversion above uses signed two's-complement W=16/F=15, exactly PCM/32768.

create_ip -name xfft -vendor xilinx.com -library ip -version 9.1 -module_name fp32_fft512
set_property -dict [list CONFIG.transform_length 512 \
    CONFIG.run_time_configurable_transform_length false \
    CONFIG.channels 1 CONFIG.super_sample_rates 1 \
    CONFIG.implementation_options radix_2_burst_io \
    CONFIG.data_format floating_point CONFIG.input_width 32 \
    CONFIG.phase_factor_width 25 CONFIG.output_ordering natural_order \
    CONFIG.throttle_scheme nonrealtime CONFIG.aresetn true CONFIG.aclken true \
    CONFIG.xk_index true CONFIG.ovflo false \
    CONFIG.target_clock_frequency 100 \
    CONFIG.memory_options_data block_ram CONFIG.memory_options_phase_factors block_ram \
    CONFIG.memory_options_reorder block_ram CONFIG.complex_mult_type use_mults_resources \
    CONFIG.ACLK_INTF.FREQ_HZ 100000000] [get_ips fp32_fft512]

foreach ip [get_ips] {
    set name [get_property NAME $ip]
    set report [open [file join $run_dir reports ${name}_properties.tsv] w]
    foreach property [lsort [list_property $ip]] {
        if {[string match CONFIG.* $property] || $property in {IPDEF IP_TOP IS_LOCKED UPGRADE_VERSIONS}} {
            puts $report "$property\t[get_property $property $ip]"
        }
    }
    close $report
}
report_ip_status -file [file join $run_dir reports ip_status.txt]
generate_target all [get_ips]
export_ip_user_files -of_objects [get_ips] -no_script -sync -force
set summary [open [file join $run_dir reports generation.txt] w]
puts $summary [version]
puts $summary "PART=[get_property PART [current_project]]"
puts $summary "IPS=[get_ips]"
puts $summary "PRODUCT_GENERATION=passed"
puts $summary "SYNTHESIS_RUN=false"
puts $summary "BOARD_ACCESSED=false"
close $summary
close_project
exit
