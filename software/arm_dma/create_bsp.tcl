# Offline BSP generation only: never connects, downloads or programs a device.
if {[catch {
if {$argc != 2} {error "Usage: create_bsp.tcl SYSTEM_XSA NEW_WORKSPACE"}
set xsa [file normalize [lindex $argv 0]]
set workspace [file normalize [lindex $argv 1]]
if {![file isfile $xsa] || [file exists $workspace]} {error "Missing XSA or existing workspace"}
setws $workspace
puts "XSCT_VERSION [version]"
platform create -name mfcc_dma -hw $xsa -proc ps7_cortexa9_0 -os standalone -no-boot-bsp
bsp config stdin ps7_uart_1
bsp config stdout ps7_uart_1
bsp config extra_compiler_flags {-g -Wall -Wextra -O2 -mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard}
platform generate
puts [platform report]
puts [bsp getdrivers]
app create -name dma_template -platform mfcc_dma -domain standalone_domain -template {Hello World}
app build -name dma_template
} message options]} {
 puts stderr "DMA_BSP_FAILED $message"
 if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
 exit 1
}
puts "DMA_BSP_AND_TEMPLATE_COMPLETE"
exit 0
