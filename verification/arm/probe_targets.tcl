# Read-only target discovery. No target selection, reset, download or memory writes.
puts "XSDB_VERSION [version]"
if {[catch {connect -url tcp:127.0.0.1:3121} result]} {
    puts "CONNECT_ERROR $result"
    exit 1
}
puts "CONNECTION $result"
puts "TARGETS_BEGIN"
puts [targets]
puts "TARGETS_END"
puts "JTAG_TARGETS_BEGIN"
puts [jtag targets]
puts "JTAG_TARGETS_END"
puts "TARGET_PROPERTIES_BEGIN"
puts [targets -target-properties]
puts "TARGET_PROPERTIES_END"
disconnect
exit
