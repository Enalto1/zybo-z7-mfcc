# Run with Vitis 2024.2 xsct.bat. This creates no boot image or bitstream.
if {$argc != 2} { error "Usage: create_bsp.tcl XSA NEW_WORKSPACE" }
set xsa [file normalize [lindex $argv 0]]
set workspace [file normalize [lindex $argv 1]]
if {![file isfile $xsa]} { error "XSA missing: $xsa" }
if {[file exists $workspace]} { error "Refusing existing workspace: $workspace" }
setws $workspace
puts "XSCT_VERSION [version]"
platform create -name zybo_z7_20_ps -hw $xsa -proc ps7_cortexa9_0 -os standalone -no-boot-bsp
puts [platform report]
puts [domain list]
bsp config stdin ps7_uart_1
bsp config stdout ps7_uart_1
bsp config extra_compiler_flags {-g -Wall -Wextra -O2 -mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard -fno-fast-math -ffp-contract=off -fexcess-precision=standard}
platform generate
puts [bsp getdrivers]
app create -name hello_template -platform zybo_z7_20_ps -domain standalone_domain -template {Hello World}
app build -name hello_template
puts "BSP_AND_TEMPLATE_COMPLETE"
exit
