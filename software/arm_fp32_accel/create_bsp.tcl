# New accelerator XSA only; no board connection, ELF download or boot image.
if {[catch {
if {$argc != 2} { error "Usage: create_bsp.tcl SYSTEM_XSA NEW_WORKSPACE" }
set xsa [file normalize [lindex $argv 0]]
set workspace [file normalize [lindex $argv 1]]
if {![file isfile $xsa]} { error "System XSA missing: $xsa" }
if {[file exists $workspace]} { error "Refusing existing workspace: $workspace" }
setws $workspace
puts "XSCT_VERSION [version]"
platform create -name mfcc_fp32_accel -hw $xsa -proc ps7_cortexa9_0 -os standalone -no-boot-bsp
bsp config stdin ps7_uart_1
bsp config stdout ps7_uart_1
bsp config extra_compiler_flags {-g -Wall -Wextra -O2 -mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard}
platform generate
puts [platform report]
puts [bsp getdrivers]
app create -name accel_template -platform mfcc_fp32_accel -domain standalone_domain -template {Hello World}
app build -name accel_template
} message options]} {
    puts stderr "FP32_ACCEL_BSP_FAILED $message"
    if {[dict exists $options -errorinfo]} { puts stderr [dict get $options -errorinfo] }
    exit 1
}
puts "FP32_ACCEL_BSP_AND_TEMPLATE_COMPLETE"
exit 0
