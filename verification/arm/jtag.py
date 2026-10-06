"""Generate guarded XSCT 2024.2 scripts; hardware execution is opt-in.

Command syntax checked against the installed xsdb.tcl help. These scripts have
not been run on a board until a run manifest records successful execution.
"""
from __future__ import annotations
from pathlib import Path


def word(value) -> str:
    # Braced Tcl word; reject characters that could close it or join lines.
    text=str(value).replace('\\','/')
    if any(ch in text for ch in '{}\n\r'):
        raise ValueError('Unsafe Tcl argument')
    return '{'+text+'}'


def selection(url: str, target_filter: str, cable_serial: str, *, connect: bool=True) -> str:
    if not target_filter or not cable_serial:
        raise ValueError('Execution requires explicit CPU target filter and observed cable serial')
    prefix=f'connect -url {word(url)}\n' if connect else ''
    return prefix+f'''set choices [targets -target-properties -filter {word(target_filter)}]
set target_discovery_deadline [expr {{[clock milliseconds] + 5000}}]
for {{set target_discovery_retry 0}} {{[llength $choices] == 0 && $target_discovery_retry < 50}} {{incr target_discovery_retry}} {{
    set target_discovery_wait [expr {{min(100, $target_discovery_deadline - [clock milliseconds])}}]
    if {{$target_discovery_wait <= 0}} {{break}}
    after $target_discovery_wait
    set choices [targets -target-properties -filter {word(target_filter)}]
}}
if {{[llength $choices] != 1}} {{error "Expected exactly one selected CPU target"}}
set chosen [lindex $choices 0]
if {{![dict exists $chosen jtag_cable_serial] || [dict get $chosen jtag_cable_serial] ne {word(cable_serial)}}} {{error "Observed JTAG cable serial differs"}}
if {{![dict exists $chosen name] || ![string match "*Cortex-A9*#0*" [dict get $chosen name]]}} {{error "Selected target is not Cortex-A9 CPU0"}}
puts "SELECTED_TARGET $chosen"
targets -set [dict get $chosen target_id]
'''


def reset_initialization(config: dict) -> str:
    """Reset an already selected CPU0 system, halt CPU1, and restore its PS XSA.

    Use once before each ELF download, never between READY/input and execution.
    The observed L2 control is recorded; it does not prove every cache tag reset.
    """
    script='''rst -system -stop
set reset_cpu1_deadline [expr {[clock milliseconds] + 5000}]
set reset_cpu1_choices {}
for {set reset_cpu1_retry 0} {$reset_cpu1_retry <= 50} {incr reset_cpu1_retry} {
    set reset_cpu1_choices {}
    foreach reset_candidate [targets -target-properties] {
'''+f'''        if {{[dict exists $reset_candidate jtag_cable_serial] && [dict exists $reset_candidate name] &&
            [dict get $reset_candidate jtag_cable_serial] eq {word(config['cable_serial'])} &&
            [string match "*Cortex-A9*#1" [dict get $reset_candidate name]]}} {{
            lappend reset_cpu1_choices $reset_candidate
        }}
'''+'''    }
    if {[llength $reset_cpu1_choices] != 0} {break}
    set reset_cpu1_wait [expr {min(100, $reset_cpu1_deadline - [clock milliseconds])}]
    if {$reset_cpu1_wait <= 0 || $reset_cpu1_retry == 50} {break}
    after $reset_cpu1_wait
}
if {[llength $reset_cpu1_choices] != 1} {error "Expected exactly one same-cable CPU1 after system reset"}
set reset_cpu1 [lindex $reset_cpu1_choices 0]
puts "RESET_CPU1_TARGET $reset_cpu1"
targets -set [dict get $reset_cpu1 target_id]
if {[catch {stop} stop_error stop_options]} {
    if {[string trim $stop_error] ne "Already stopped"} {return -options $stop_options $stop_error}
}
puts "RESET_CPU1_HALTED_PC [rrd -nvlist pc]"
'''
    script+=selection(config['url'],config['target_filter'],config['cable_serial'],connect=False)
    script+='''if {[catch {stop} stop_error stop_options]} {
    if {[string trim $stop_error] ne "Already stopped"} {return -options $stop_options $stop_error}
}
puts "RESET_CPU0_HALTED_PC [rrd -nvlist pc]"
puts "POST_SYSTEM_RESET_L2_CONTROL [mrd -value 0xF8F02100]"
'''
    script+=f'source {word(config["ps7_init"])}\nps7_init\nps7_post_config\n'
    return script


def require_pc(address: int) -> str:
    # Target properties can be stale across debugger sessions. A register read
    # must succeed on the actually stopped CPU at the intended breakpoint.
    return ('set breakpoint_pc_hex [dict get [rrd -nvlist pc] pc]\n'
            'if {![regexp {^[0-9a-fA-F]+$} $breakpoint_pc_hex]} {error "Unreadable CPU PC"}\n'
            'scan $breakpoint_pc_hex %x breakpoint_pc\n'
            f'if {{$breakpoint_pc != {address}}} {{error "CPU is not at expected breakpoint: $breakpoint_pc_hex"}}\n')


def memory_file(address: int, size: int, path: Path) -> str:
    if size==0:
        return f'set empty [open {word(path)} wb]; close $empty\n'
    # XSCT -bin writes the raw Memory.get blob unchanged. Word accesses retain
    # the exact byte extent only when both address and size are four-aligned;
    # otherwise XSCT would align the address down, so keep byte mode.
    if address%4==0 and size%4==0:
        return f'mrd -size w -bin -file {word(path)} 0x{address:x} {size//4}\n'
    return f'mrd -size b -bin -file {word(path)} 0x{address:x} {size}\n'


def prepare_job(config: dict, symbols: dict, elf: Path, pcm: Path | None, out: Path, *, initialize: bool=False) -> str:
    script=selection(config['url'],config['target_filter'],config['cable_serial'])
    # initialize is retained for callers; every new ELF now gets the same full
    # system/PS initialization, including a transition from hello to MFCC.
    script+=reset_initialization(config)
    script+=f'dow {word(elf)}\nset ready_bp [bpadd -addr 0x{symbols["arm_ready_breakpoint"]:x} -type hw]\n'
    script+=f'con -block -timeout {config["timeout"]}\n'
    script+=require_pc(symbols['arm_ready_breakpoint'])
    script+='bpremove $ready_bp\n'
    script+=memory_file(symbols['arm_status'],128,out/'ready_status.bin')
    script+=memory_file(symbols['arm_layout'],128,out/'layout.bin')
    script+=memory_file(symbols['arm_clock_registers'],16,out/'clock_registers.bin')
    if pcm is not None:
        if pcm.stat().st_size:
            script+=f'dow -data {word(pcm)} 0x{symbols["arm_pcm"]:x}\n'
        script+=memory_file(symbols['arm_pcm'],pcm.stat().st_size,out/'pcm_readback.bin')
    script+='puts "READY_AND_READBACK_COMPLETE_HOST_MUST_VERIFY_SHA256"\n'
    return script


def execute_job(config: dict, symbols: dict, out: Path, layout: dict, samples: int, frames: int,
                mode: int, repeats: int=0) -> str:
    script=selection(config['url'],config['target_filter'],config['cable_serial'])
    # Status is re-read to ensure a lost/replaced debugger session fails closed.
    script+=require_pc(symbols['arm_ready_breakpoint'])
    script+=f'set state [mrd -value 0x{symbols["arm_status"]+8:x}]\nif {{$state != 1}} {{error "CPU is not at READY state"}}\n'
    script+=f'dow -data {word(out/"control.bin")} 0x{symbols["arm_control"]:x}\n'
    script+=f'set result_bp [bpadd -addr 0x{symbols["arm_result_breakpoint"]:x} -type hw]\n'
    script+=f'mwr 0x{symbols["arm_control"]+8:x} 1\ncon -block -timeout {config["timeout"]}\n'
    script+=require_pc(symbols['arm_result_breakpoint'])
    script+='bpremove $result_bp\n'
    script+=memory_file(symbols['arm_status'],128,out/'result_status.bin')
    if mode!=3:
        script+=memory_file(symbols['arm_results'],frames*layout['result_bytes'],out/'results.bin')
    if mode==2:
        script+=memory_file(symbols['arm_preemphasis'],samples*4,out/'preemphasis.bin')
        if frames:
            script+=memory_file(symbols['arm_trace'],layout['trace_bytes'],out/'trace.bin')
    if mode==3:
        script+=memory_file(symbols['arm_timing_records'],repeats*16,out/'timing_records.bin')
    script+='puts "RESULT_READBACK_COMPLETE"\n'
    return script
