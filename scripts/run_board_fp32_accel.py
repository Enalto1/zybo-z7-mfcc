"""Offline-first, dedicated FP32 PS/PL board validation (never starts a server).

Execute only after the physical board/cable and PS/DDR bring-up have been checked.
Mode 0 smoke gates mode 1 full development PCM, which gates timing trials.
Each trial resets the PS system, initializes it from the same XSA and reloads
the unmodified ELF; discarded trials do not warm the next one.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import struct
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
import zipfile
import zlib

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
BUILD = PROJECT.parent / 'build'
ARM = BUILD / 'arm_fp32_accel/p01'
SYSTEM = BUILD / 'system_fp32/f01'
LAYOUT = (0x46504143, 1, 32, 128, 24, 88, 40, 262144, 21268, 2,
          8, 12, 16, 20, 0x43c00000, 0xc556a8e8)
STAT_NAMES = ('polls samples_sent records_received expected_frames status error_flags '
              'input_written input_consumed output_captured output_popped '
              'last_lo last_hi last_frame last_meta failed_offset abort_attempted abort_failed core_error_detail').split()
BOUNDARY = ('Timer starts after identity probe, before ABORT/SAMPLE_COUNT/START; ends after '
            'drain/feed polling, DDR result stores, all POPs, counter checks and hardware-cycle reads. '
            'Excludes PS system reset/init, FPGA programming, initial PCM CRC, JTAG/UART, cache flush, numeric comparison, and ELF startup. '
            'Hardware busy cycles include PS-induced stalls, not pure PL compute time. '
            'CPU is occupied by polling. Each trial resets the PS system, halts CPU1, initializes '
            'the same-XSA PS configuration, programs the pinned bit after reset, applies post_config and reloads the ELF; discarded '
            'trials do not establish warm caches for later trials.')
STARTUP_POLICY = ('Every job: shared system reset/CPU1 halt/same-XSA PS initialization, '
                  'program pinned FP32 system bit, require configured FPGA state, ps7_post_config, ELF download. '
                  '--initialize is retained for CLI compatibility and does not disable per-job programming.')
sys.path.insert(0, str(PROJECT / 'verification/arm'))
import jtag
from clocks import expected_configuration, verify_configuration
from uart import Capture
sys.path.insert(0, str(PROJECT / 'verification/arm_fp32_accel'))
import references


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Elf:
    """Read actual ELF32 ARM symbols and initialized LOAD bytes without tool output assumptions."""
    def __init__(self, path):
        self.raw = Path(path).read_bytes()
        h = struct.unpack_from('<16sHHIIIIIHHHHHH', self.raw)
        require(h[0][:7] == b'\x7fELF\x01\x01\x01' and h[1:3] == (2, 40), 'Expected ARM ELF32 LE executable')
        require(h[4] == 0x100000 and h[7] == 0x05000400, 'Unexpected ELF entry/ABI')
        self.loads = [p for i in range(h[10]) if (p := struct.unpack_from('<8I', self.raw, h[5]+i*h[9]))[0] == 1]
        for p in self.loads:
            require(p[2] == p[3] and 0x100000 <= p[2] and p[2]+p[5] <= 0x2100000, 'ELF LOAD outside reserved DDR')
            require(p[4] <= p[5] and p[1]+p[4] <= len(self.raw), 'Invalid ELF LOAD size')
        sections = [struct.unpack_from('<10I', self.raw, h[6]+i*h[11]) for i in range(h[12])]
        self.symbols = {}
        for tab in sections:
            if tab[1] != 2:
                continue
            strings = sections[tab[6]]
            names = self.raw[strings[4]:strings[4]+strings[5]]
            for pos in range(tab[4], tab[4]+tab[5], tab[9]):
                name, address, size, _, _, _ = struct.unpack_from('<IIIBBH', self.raw, pos)
                if name:
                    key = names[name:names.index(b'\0', name)].decode('ascii')
                    self.symbols[key] = {'address': address, 'bytes': size}

    def at_symbol(self, name):
        address, size = self.symbols[name]['address'], self.symbols[name]['bytes']
        for p in self.loads:
            if p[2] <= address and address+size <= p[2]+p[4]:
                start = p[1]+address-p[2]
                return self.raw[start:start+size]
        raise ValueError('No initialized ELF bytes for ' + name)


def identity(out, arm_run=ARM, system_run=SYSTEM):
    arm_run, system_run = Path(arm_run).resolve(), Path(system_run).resolve()
    require(arm_run.is_relative_to((BUILD/'arm_fp32_accel').resolve()), 'ARM run must remain under build/arm_fp32_accel')
    require(system_run.is_relative_to((BUILD/'system_fp32').resolve()), 'System run must remain under build/system_fp32')
    manifest, system = read(arm_run/'build_manifest.json'), read(system_run/'run_manifest.json')
    require(manifest['status'] == system['status'] == 'complete', 'Unsuccessful ARM/system build')
    for root in (arm_run, system_run):
        for relative, digest in read(root/'artifact_hashes.json').items():
            path = (root/relative).resolve()
            require(path.is_relative_to(root) and sha(path) == digest, 'Changed prepared artifact: '+str(path))
    source_hashes = system['source_sha256']
    require(source_hashes['hardware/fp32/rtl/fp32_backend.sv'] == references.BACKEND_SHA, 'System arithmetic backend differs from RTLgold')
    required_abi = dict(version=0x00010001, core_id=2, output_format=0x00030020,
                        contract_tag=0xc556a8e8, error_detail_offset=0x64)
    require(all(system['abi'].get(k) == v for k, v in required_abi.items()), 'System transport identity differs from FP32 host')
    require(system['baseline']['continuous_index_sha256'] == references.GOLD_INDEX_SHA, 'System used different continuous RTLgold')
    for name in ('vendor_AXI_simulation', 'board_implementation', 'PS_configuration', 'PS_bus_model_simulation'):
        require(system['validation'][name] == 'PASS', 'System gate not passed: '+name)
    require(system['timing']['setup_slack_ns'] >= 0 and system['timing']['hold_slack_ns'] >= 0, 'FP32 system timing not closed')
    for name, digest in manifest['sources'].items():
        require(sha(arm_run/'source'/name) == digest, 'Changed compiled source snapshot: '+name)
    a = manifest['arm']
    elf, xsa = Path(a['elf']), Path(a['xsa'])
    bit, init = system_run/'design/zybo_z7_20_fp32.bit', arm_run/'ps7_init.tcl'
    require(elf.resolve().is_relative_to(arm_run), 'ELF must belong to selected ARM run')
    require(xsa.resolve() == (system_run/'design/zybo_z7_20_fp32.xsa').resolve(), 'ARM ELF must use selected FP32 system XSA')
    for name, path, digest in (('xsa', xsa, a['xsa_sha256']), ('bitstream', bit, a['bitstream_sha256'])):
        require(sha(path) == digest == system['outputs'][name]['sha256'], 'System/ARM artifact binding failed: '+name)
    for path, digest in ((elf, a['elf_sha256']), (init, a['ps7_init_sha256'])):
        require(sha(path) == digest, 'Changed prepared artifact: '+str(path))
    with zipfile.ZipFile(xsa) as archive:
        bits = [n for n in archive.namelist() if n.endswith('.bit')]
        require(len(bits) == 1 and hashlib.sha256(archive.read(bits[0])).hexdigest() == sha(bit), 'XSA bitstream binding failed')
        require(archive.read('ps7_init.tcl') == init.read_bytes(), 'XSA PS init binding failed')
        hwh = [n for n in archive.namelist() if n.endswith('.hwh')]
        require(len(hwh) == 1, 'Expected one HWH')
        params = {p.get('NAME'): p.get('VALUE') for p in ET.fromstring(archive.read(hwh[0])).iter('PARAMETER')}
    binary = Elf(elf)
    for name, record in a['symbols'].items():
        require(binary.symbols[name] == record, 'ELF symbol differs from manifest: '+name)
    mmu_table = binary.symbols['MMUTable']['address']
    require(mmu_table % 16384 == 0 and 0x100000 <= mmu_table < 0x2100000-16384, 'ELF MMUTable bounds/alignment')
    layout = binary.at_symbol('arm_fp32_accel_layout')
    require(struct.unpack('<16I', layout) == LAYOUT, 'Actual ELF layout differs from audited ABI')
    bundle = references.load()
    gold_freeze = read(references.GOLD/'freeze.json')
    arithmetic_sources = {k: v for k, v in gold_freeze['source_hashes'].items() if k.startswith('hardware/fp32/rtl/')}
    require(len(arithmetic_sources) == 6 and all(source_hashes.get(k) == v for k, v in arithmetic_sources.items()),
            'One or more of six frozen arithmetic RTL files changed')
    require(system['baseline']['ip_manifest_sha256'] == gold_freeze['ip_manifest_sha256']
            and source_hashes['verification/c/tolerances.json'] == references.TOLERANCE_SHA,
            'System IP revision or numeric tolerances differ')
    gold_index = read(references.GOLD/'artifact_manifest.json')
    for name, digest in gold_index.items():
        if name.startswith('coefficients/') and name.endswith('.mem'):
            require(sha(system_run/name) == digest, 'System coefficient ROM differs from frozen gold: '+name)
    pcm, reference = bundle['pcm'], bundle['records']
    require(binary.at_symbol('accel_smoke_pcm') == pcm[:1024], 'Embedded smoke PCM differs')
    require(binary.at_symbol('accel_smoke_expected') == struct.pack('<13Q', *bundle['bits'][:13]), 'Embedded smoke expected differs')
    require(manifest['smoke']['reference'] == bundle['metadata'], 'ARM/reference provenance differs')
    for name, data in [('development_pcm.bin', pcm), ('expected_records.bin', reference), ('elf_layout.bin', layout)]:
        (out/name).write_bytes(data)
    dump(out/'reference_identity.json', bundle['metadata'])
    xp = (Path(a['bsp_include'])/'xparameters.h').read_text()
    cpu = int(re.search(r'#define XPAR_CPU_CORTEXA9_0_CPU_CLK_FREQ_HZ\s+(\d+)', xp)[1])
    result = dict(arm_run=str(arm_run), system_run=str(system_run),
                  arm_manifest_sha256=sha(arm_run/'build_manifest.json'), system_manifest_sha256=sha(system_run/'run_manifest.json'),
                  arm_artifact_index_sha256=sha(arm_run/'artifact_hashes.json'), system_artifact_index_sha256=sha(system_run/'artifact_hashes.json'),
                  elf=str(elf), xsa=str(xsa), bit=str(bit), ps7_init=str(init),
                  elf_sha256=sha(elf), xsa_sha256=sha(xsa), bit_sha256=sha(bit), ps7_init_sha256=sha(init),
                  symbols={**a['symbols'], 'MMUTable': binary.symbols['MMUTable']},
                  mmio_descriptor_address=mmu_table+(0x43c00000>>20)*4, required_mmio_descriptor=0x43c00c16,
                  actual_elf_layout=list(LAYOUT), cpu_bsp_hz=cpu, compiler=a['compiler'],
                  compiler_sha256=a['compiler_sha256'], flags=a['flags'], bsp_xparameters_sha256=a['xparameters_sha256'],
                  bsp_libxil_sha256=a['libxil_sha256'], expected_clocks=expected_configuration(xsa, init),
                  pl_xsa_mhz=params.get('PCW_FPGA0_PERIPHERAL_FREQMHZ'), pcm_sha256=references.PCM_SHA,
                  case=bundle['metadata']['case'], reference=bundle['metadata'],
                  numerical_accuracy_status='DEVELOPMENT_ONLY_NOT_ALL_CASES_ACCEPTED',
                  timing_boundary=BOUNDARY, startup_policy=STARTUP_POLICY,
                  cache_source_state='Explicit I/D caches enabled; every job records CPU/MMU/L2/FPSCR snapshots',
                  compiled_source_hashes=manifest['sources'], system_source_hashes=source_hashes)
    dump(out/'identity.json', result)
    return result, pcm, reference, bundle


def decode_status(data):
    require(len(data) == 128, 'Status length')
    leading = struct.unpack_from('<IIIiIIIIQ', data)
    names = ('magic version state result numeric_checked numeric_passed failure_index pcm_crc32 timer_hz').split()
    result = dict(zip(names, leading))
    ticks, cycles = struct.unpack_from('<QQ', data, 40)
    result['transport'] = dict(elapsed_ticks=ticks, hardware_busy_cycles=cycles,
                               **dict(zip(STAT_NAMES, struct.unpack_from('<18I', data, 56))))
    return result


def compare(data, reference, status, mode, pcm):
    """Retain all diagnostics: a failed board result is returned, never silently discarded."""
    errors = []
    samples, frames, count = len(pcm)//2, (len(pcm)//2-512)//160+1, len(reference)//24
    checks = {'magic': 0x46504143, 'version': 1, 'state': 3, 'result': 0,
              'numeric_checked': int(mode == 0), 'numeric_passed': int(mode == 0),
              'failure_index': 0xffffffff, 'pcm_crc32': zlib.crc32(pcm)}
    for name, expected in checks.items():
        if status[name] != expected:
            errors.append(f'{name}: {status[name]} != {expected}')
    t = status['transport']
    for name in ('elapsed_ticks', 'hardware_busy_cycles'):
        if t[name] <= 0:
            errors.append(f'transport.{name}: nonempty clip requires a positive count')
    if status['timer_hz'] <= 0:
        errors.append('timer_hz must be positive')
    counts = {'samples_sent': samples, 'records_received': count, 'expected_frames': frames,
              'status': 2, 'error_flags': 0, 'input_written': samples, 'input_consumed': samples,
              'output_captured': count, 'output_popped': count, 'failed_offset': 0xffffffff,
              'abort_attempted': 0, 'abort_failed': 0, 'core_error_detail': 0}
    if reference:
        last_q, last_frame, last_index, last_bfp, last_flags = struct.unpack_from('<QIIiI', reference, len(reference)-24)
        counts.update(last_lo=last_q & 0xffffffff, last_hi=(last_q>>32) & 0xffffffff,
                      last_frame=last_frame, last_meta=last_index | ((last_bfp & 255)<<8) | (last_flags<<16))
    for name, expected in counts.items():
        if t[name] != expected:
            errors.append(f'transport.{name}: {t[name]} != {expected}')
    if len(data) != len(reference):
        errors.append(f'result bytes: {len(data)} != {len(reference)}')
    mismatch = []
    fields = ['value_bits', 'frame', 'index', 'bfp_s', 'flags']
    for i in range(min(len(data), len(reference))//24):
        actual, wanted = struct.unpack_from('<QIIiI', data, i*24), struct.unpack_from('<QIIiI', reference, i*24)
        if actual != wanted:
            mismatch.append(dict(record=i, differences={k: [a, e] for k, a, e in zip(fields, actual, wanted) if a != e}))
    if mismatch:
        errors.append(f'{len(mismatch)} records mismatch')
    return dict(passed=not errors, errors=errors, mismatched_records=len(mismatch), mismatches=mismatch,
                expected_samples=samples, expected_frames=frames, expected_records=count,
                raw_value_and_metadata_bit_exact=data == reference,
                result_sha256=hashlib.sha256(data).hexdigest(), numeric_accuracy_status='DEVELOPMENT_SCOPE_ONLY')


def clock_capture(symbols, out):
    # Raw read-only CPU registers allow inspection of actual cache/FPSCR state.
    return (jtag.memory_file(0xF8000100, 12, out/'pll_registers.bin') +
            jtag.memory_file(0xF8000120, 4, out/'arm_clock.bin') +
            jtag.memory_file(0xF8000170, 4, out/'fpga_clock.bin') +
            jtag.memory_file(0xF8F00208, 4, out/'global_timer_control.bin') +
            jtag.memory_file(0xF8F02100, 4, out/'l2_cache_control.bin') +
            jtag.memory_file(symbols['MMUTable']+(0x43c00000>>20)*4, 4, out/'mmio_descriptor.bin') +
            f'set rf [open {jtag.word(out/"cpu_registers.txt")} w]\n'
            'foreach regpath {{pc} {cpsr} {vfp fpscr} {cp15 c1 sctlr} {cp15 c2 ttbr0} {cp15 c2 ttbcr} {cp15 c0 id_mmfr0}} {\n'
            '  puts $rf "REGISTER_PATH $regpath"\n'
            '  puts $rf [rrd -nvlist {*}$regpath]\n'
            '}\nclose $rf\n')


def guarded_continue(bp_address, timeout, diagnostic):
    # Even breakpoint timeout attempts a bounded halt and captures raw status.
    return (f'set owned_bp [bpadd -addr 0x{bp_address:x} -type hw]\n'
            f'set run_failed [catch {{con -block -timeout {timeout}}} run_message]\n'
            'catch {bpremove $owned_bp}\n'
            'if {$run_failed} {catch {stop}; puts "EXECUTION_FAILURE $run_message"}\n' + diagnostic +
            'if {$run_failed} {error $run_message}\n' + require_pc(bp_address))


def require_pc(address):
    # -nvlist emits bare hexadecimal strings in installed XSCT print_regs.
    return ('set pc_hex [dict get [rrd -nvlist pc] pc]\n'
            'if {[scan $pc_hex %x pc_int] != 1} {error "Unreadable CPU PC"}\n'
            f'if {{$pc_int != {address}}} {{error "CPU did not stop at expected breakpoint: $pc_hex"}}\n')


def prepare_tcl(args, ident, destination, initialize=False):
    s = {name: entry['address'] for name, entry in ident['symbols'].items()}
    script = jtag.selection(args.url, args.target_filter, args.cable_serial)
    # Root's accel_01 board capture confirmed FPGA unconfigured after system
    # reset. Run the shared reset/PS init first, then program after that reset.
    script += jtag.reset_initialization(dict(url=args.url, target_filter=args.target_filter,
                                            cable_serial=args.cable_serial, ps7_init=ident['ps7_init']))
    require(args.fpga_target_filter, 'Every system reset requires observed --fpga-target-filter to reprogram PL')
    script += f'''set fpga_choices [targets -target-properties -filter {jtag.word(args.fpga_target_filter)}]
if {{[llength $fpga_choices] != 1}} {{error "Expected exactly one selected FPGA"}}
set selected_fpga [lindex $fpga_choices 0]
if {{![dict exists $selected_fpga jtag_cable_serial] || [dict get $selected_fpga jtag_cable_serial] ne {jtag.word(args.cable_serial)}}} {{error "FPGA cable differs"}}
if {{![string match -nocase "*xc7z020*" [dict get $selected_fpga name]]}} {{error "FPGA is not xc7z020"}}
targets -set [dict get $selected_fpga target_id]
fpga -file {jtag.word(ident['bit'])}
set programmed_state [fpga -state]
puts "FPGA_POST_PROGRAM_STATE $programmed_state"
puts "FPGA_POST_PROGRAM_CONFIG_STATUS [fpga -config-status]"
if {{[string trim $programmed_state] ne "FPGA is configured"}} {{error "FPGA not configured after programming"}}
targets -set [dict get $chosen target_id]
'''
    # System reset can reset the PL as well. Reprogram on every trial; the
    # firmware still probes ID/ABI/tag before any peripheral writes.
    script += 'ps7_post_config\n'
    script += f'dow {jtag.word(ident["elf"])}\n'
    script += guarded_continue(s['arm_fp32_accel_ready_breakpoint'], args.timeout,
                               jtag.memory_file(s['arm_fp32_accel_status'], 128, destination/'ready_status.bin'))
    script += jtag.memory_file(s['arm_fp32_accel_layout'], 64, destination/'layout.bin')
    script += clock_capture(s, destination)
    script += f'set pcvalue [rrd pc]\nputs "READY_PC $pcvalue"\n'
    script += 'puts "READY_CAPTURE_COMPLETE"\nexit\n'
    return script


def input_tcl(args, ident, destination, samples, mode):
    s = {name: entry['address'] for name, entry in ident['symbols'].items()}
    script = jtag.selection(args.url, args.target_filter, args.cable_serial)
    script += require_pc(s['arm_fp32_accel_ready_breakpoint'])
    script += f'if {{[mrd -value 0x{s["arm_fp32_accel_status"]+8:x}] != 1}} {{error "Lost READY state"}}\n'
    if mode == 1:
        script += f'dow -data {jtag.word(destination/"input_pcm.bin")} 0x{s["arm_fp32_accel_pcm"]:x}\n'
    script += jtag.memory_file(s['arm_fp32_accel_pcm'], samples*2, destination/'pcm_readback.bin')
    script += 'puts "PCM_READBACK_COMPLETE_HOST_MUST_VERIFY"\nexit\n'
    return script


def execute_tcl(args, ident, destination, records):
    s = {name: entry['address'] for name, entry in ident['symbols'].items()}
    script = jtag.selection(args.url, args.target_filter, args.cable_serial)
    script += require_pc(s['arm_fp32_accel_ready_breakpoint'])
    script += f'if {{[mrd -value 0x{s["arm_fp32_accel_status"]+8:x}] != 1}} {{error "Lost READY state"}}\n'
    script += f'dow -data {jtag.word(destination/"control.bin")} 0x{s["arm_fp32_accel_control"]:x}\n'
    diagnostic = jtag.memory_file(s['arm_fp32_accel_status'], 128, destination/'result_status.bin')
    diagnostic += jtag.memory_file(s['arm_fp32_accel_results'], records*24, destination/'results.bin')
    diagnostic += f'puts "RESULT_PC [rrd pc]"\n'
    script += guarded_continue(s['arm_fp32_accel_result_breakpoint'], args.timeout, diagnostic)
    script += clock_capture(s, destination/'result_environment')
    script += 'puts "RESULT_CAPTURE_COMPLETE"\nexit\n'
    return script


def run_script(args, script, path):
    # XSCT's launcher can return zero after a Tcl error. Require both an explicit
    # top-level catch/exit code and a completion token unique to this invocation.
    body = script.removesuffix('exit\n')
    sentinel = 'BOARD_ACCEL_SCRIPT_OK_'+hashlib.sha256(body.encode()).hexdigest()[:16]
    wrapped = ('if {[catch {\n'+body+'\n} board_error board_options]} {\n'
               '  puts stderr "BOARD_ACCEL_SCRIPT_FAILED $board_error"\n'
               '  if {[dict exists $board_options -errorinfo]} {puts stderr [dict get $board_options -errorinfo]}\n'
               '  exit 1\n}\nputs "'+sentinel+'"\nexit 0\n')
    path.write_text(wrapped, encoding='utf-8')
    argv = [str(args.xsct), str(path)]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=args.timeout+60)
        log = p.stdout+'\n'+p.stderr
        path.with_suffix('.log').write_text(log, encoding='utf-8')
        require(p.returncode == 0 and sentinel in p.stdout.splitlines(), 'XSCT failed or missing completion sentinel: '+str(path))
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b''
        if isinstance(output, bytes):
            output = output.decode(errors='replace')
        path.with_suffix('.log').write_text(output+'\nHOST_TIMEOUT\n', encoding='utf-8')
        raise


def check_clocks(destination, ident, status):
    raw = (destination/'pll_registers.bin').read_bytes()+(destination/'arm_clock.bin').read_bytes()
    decoded = verify_configuration(raw, ident['expected_clocks'],
                                   dict(cpu_hz=ident['cpu_bsp_hz'], timer_hz=status['timer_hz']))
    fpga = struct.unpack('<I', (destination/'fpga_clock.bin').read_bytes())[0]
    require(fpga & 0x03f03f30 == 0x00200500, 'FCLK0 register differs from FP32 PS init')
    io = struct.unpack_from('<I', raw, 8)[0]
    require(io & 0x13 == 0, 'IO PLL reset/powerdown/bypass state')
    hz = ident['expected_clocks']['ps_input_configured_hz']*((io>>12)&127)/((fpga>>8)&63)/((fpga>>20)&63)
    require(abs(hz-100000000) < 1000, 'FCLK0 does not corroborate 100 MHz')
    timer_control = struct.unpack('<I', (destination/'global_timer_control.bin').read_bytes())[0]
    l2_control = struct.unpack('<I', (destination/'l2_cache_control.bin').read_bytes())[0]
    require(timer_control & 1 and ((timer_control>>8)&255) == 0, 'Global timer must be enabled with zero prescaler')
    require(l2_control & 1 == 1, 'Expected L2 cache enabled during application')
    registers = (destination/'cpu_registers.txt').read_text()
    cpu_regs = {}
    for name in ('pc', 'cpsr', 'fpscr', 'sctlr', 'ttbr0', 'ttbcr', 'id_mmfr0'):
        match = re.search(r'^'+name+r'\s+([0-9a-fA-F]+)\s*$', registers, re.M)
        require(match is not None, 'Missing targeted CPU register: '+name)
        cpu_regs[name] = int(match[1], 16)
    require(cpu_regs['sctlr'] & 0x1005 == 0x1005, 'Expected MMU/I-cache/D-cache enabled')
    descriptor = struct.unpack('<I', (destination/'mmio_descriptor.bin').read_bytes())[0]
    require(cpu_regs['ttbr0'] & 0xffffc000 == ident['symbols']['MMUTable']['address'] and cpu_regs['ttbcr'] == 0,
            'Runtime MMU translation table differs from ELF MMUTable')
    require(cpu_regs['id_mmfr0'] & 15 == 3, 'Expected Cortex-A9 VMSAv7 without optional PXN')
    require(descriptor == ident['required_mmio_descriptor'],
            f'MMIO descriptor {descriptor:#010x} must be Cortex-A9 Device/XN 0x43c00c16 (PXN bit0 clear)')
    decoded.update(fpga_clk0_ctrl=fpga, pl_configured_hz=hz, physical_oscillator_measured=False,
                   global_timer_control=timer_control, l2_cache_control=l2_control,
                   cpu_registers=cpu_regs, instruction_cache_enabled=True, data_cache_enabled=True,
                   mmu_enabled=True, l2_cache_enabled=bool(l2_control & 1),
                   mmio_descriptor_address=ident['mmio_descriptor_address'], mmio_descriptor=descriptor)
    return decoded


def job(args, ident, pcm, expected, mode, destination, capture, bundle, initialize=False):
    destination.mkdir(parents=True)
    (destination/'result_environment').mkdir()
    (destination/'input_pcm.bin').write_bytes(pcm)
    mark = capture.marker() if capture else 0
    run_script(args, prepare_tcl(args, ident, destination, initialize), destination/'prepare.tcl')
    ready = decode_status((destination/'ready_status.bin').read_bytes())
    dump(destination/'ready_status.json', ready)
    require(ready['magic'] == LAYOUT[0] and ready['version'] == 1 and ready['state'] == 1, 'Invalid READY')
    require(struct.unpack('<16I', (destination/'layout.bin').read_bytes()) == LAYOUT, 'Runtime layout mismatch')
    clocks = check_clocks(destination, ident, ready)
    dump(destination/'clocks.json', clocks)
    if capture:
        capture.require('ARM_FP32_ACCEL_READY_V1', mark)
    run_script(args, input_tcl(args, ident, destination, len(pcm)//2, mode), destination/'input.tcl')
    require((destination/'pcm_readback.bin').read_bytes() == pcm, 'PCM readback differs; control not submitted')
    control = (LAYOUT[0], 1, mode, len(pcm)//2, args.firmware_timeout, args.max_polls, zlib.crc32(pcm), 0)
    (destination/'control.bin').write_bytes(struct.pack('<8I', *control))
    execution_error = None
    try:
        run_script(args, execute_tcl(args, ident, destination, len(expected)//24), destination/'execute.tcl')
    except Exception as error:
        execution_error = str(error)
    # Raw files and parsed status survive failures, including partial output.
    if not (destination/'result_status.bin').exists():
        raise RuntimeError(execution_error or 'Result status unavailable')
    status = decode_status((destination/'result_status.bin').read_bytes())
    dump(destination/'result_status.json', status)
    data = (destination/'results.bin').read_bytes() if (destination/'results.bin').exists() else b''
    comparison = compare(data, expected, status, mode, pcm)
    if not len(pcm)//2 <= status['transport']['polls'] <= args.max_polls:
        comparison['passed'] = False
        comparison['errors'].append('Poll count outside submitted finite budget/input lower bound')
    if status['transport']['elapsed_ticks'] >= args.firmware_timeout*status['timer_hz']:
        comparison['passed'] = False
        comparison['errors'].append('Measured interval reached submitted firmware timeout')
    if comparison['passed']:
        comparison['numeric'] = references.numeric(data, bundle)
        if not comparison['numeric']['passed']:
            comparison['passed'] = False
            comparison['errors'].append('Python/PC C MFCC tolerance gate failed')
    if execution_error:
        comparison['passed'] = False
        comparison['errors'].append(execution_error)
    dump(destination/'comparison.json', comparison)
    require(comparison['passed'], 'Numeric/transport comparison failed: '+str(destination/'comparison.json'))
    require(status['timer_hz'] == ready['timer_hz'] and status['timer_hz'] > 0, 'Timer frequency changed/zero')
    final_clocks = check_clocks(destination/'result_environment', ident, status)
    dump(destination/'result_environment/clocks.json', final_clocks)
    require(final_clocks['cpu_registers']['sctlr'] == clocks['cpu_registers']['sctlr'] and
            final_clocks['cpu_registers']['fpscr'] == clocks['cpu_registers']['fpscr'], 'Cache/FPSCR changed during execution')
    require(final_clocks['l2_cache_control'] == clocks['l2_cache_control'] and
            final_clocks['global_timer_control'] == clocks['global_timer_control'], 'L2/timer control changed during execution')
    if capture:
        capture.require('ARM_FP32_ACCEL_RESULT_V1', mark)
    t = status['transport']
    timer_seconds = t['elapsed_ticks']/status['timer_hz']
    busy_seconds = t['hardware_busy_cycles']/clocks['pl_configured_hz']
    rounding_margin = 10/status['timer_hz']+2/clocks['pl_configured_hz']
    require(busy_seconds <= timer_seconds+rounding_margin, 'Hardware busy duration exceeds enclosing ARM interval')
    return dict(path=str(destination), samples=len(pcm)//2, frames=len(expected)//(24*13),
                passed=True, elapsed_ticks=t['elapsed_ticks'], timer_hz=status['timer_hz'],
                elapsed_seconds=timer_seconds,
                hardware_busy_cycles=t['hardware_busy_cycles'], pl_configured_hz=clocks['pl_configured_hz'],
                hardware_busy_seconds=busy_seconds)


def offline_checks(pcm, reference):
    """Fixture tests validate failure detection, not ARM or hardware execution."""
    count, samples = len(reference)//24, len(pcm)//2
    status = dict(magic=LAYOUT[0], version=1, state=3, result=0, numeric_checked=0,
                  numeric_passed=0, failure_index=0xffffffff, pcm_crc32=zlib.crc32(pcm), timer_hz=333333343,
                  transport=dict.fromkeys(STAT_NAMES, 0))
    status['transport'].update(samples_sent=samples, records_received=count, expected_frames=count//13,
                               status=2, input_written=samples, input_consumed=samples,
                               output_captured=count, output_popped=count, failed_offset=0xffffffff,
                               elapsed_ticks=1, hardware_busy_cycles=1)
    last_q, last_frame, last_index, last_bfp, last_flags = struct.unpack_from('<QIIiI', reference, len(reference)-24)
    status['transport'].update(last_lo=last_q & 0xffffffff, last_hi=(last_q>>32) & 0xffffffff,
                               last_frame=last_frame, last_meta=last_index | ((last_bfp & 255)<<8) | (last_flags<<16))
    require(compare(reference, reference, status, 1, pcm)['passed'], 'Correct fixture rejected')
    checks = 1
    for byte in (0, 7, 8, 12, 16, 20, len(reference)-1):
        changed = bytearray(reference)
        changed[byte] ^= 1
        require(not compare(changed, reference, status, 1, pcm)['passed'], 'Corrupted record accepted')
        checks += 1
    require(not compare(reference[:-24], reference, status, 1, pcm)['passed'], 'Truncated output accepted')
    checks += 1
    for key in ('state', 'result', 'numeric_checked', 'pcm_crc32'):
        changed = {**status, key: status[key]+1}
        require(not compare(reference, reference, changed, 1, pcm)['passed'], 'Bad status accepted')
        checks += 1
    for key in ('records_received', 'output_popped', 'error_flags', 'core_error_detail', 'abort_attempted', 'last_lo', 'last_hi', 'last_frame', 'last_meta'):
        changed = {**status, 'transport': {**status['transport'], key: status['transport'][key]+1}}
        require(not compare(reference, reference, changed, 1, pcm)['passed'], 'Bad transport accepted')
        checks += 1
    for key in ('elapsed_ticks', 'hardware_busy_cycles'):
        changed = {**status, 'transport': {**status['transport'], key: 0}}
        require(not compare(reference, reference, changed, 1, pcm)['passed'], 'Zero timing accepted')
        checks += 1
    require(not compare(reference, reference, {**status, 'timer_hz': 0}, 1, pcm)['passed'], 'Zero timer frequency accepted')
    checks += 1
    return dict(passed=True, checks=checks, board_execution=False, scope='Decoder/comparator corruption rejection only')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id', required=True)
    p.add_argument('--arm-run', default='p01', help='Explicit build/arm_fp32_accel run name')
    p.add_argument('--system-run', default='f01', help='Explicit build/system_fp32 run name; must match selected ARM XSA')
    p.add_argument('--output-root', type=Path, default=BUILD/'board_validation')
    action = p.add_mutually_exclusive_group()
    action.add_argument('--prepare-only', action='store_true')
    action.add_argument('--execute', action='store_true')
    p.add_argument('--xsct', type=Path, default=Path('C:/Xilinx/Vitis/2024.2/bin/xsct.bat'))
    p.add_argument('--url', default='TCP:127.0.0.1:3121')
    p.add_argument('--target-filter')
    p.add_argument('--fpga-target-filter')
    p.add_argument('--cable-serial')
    p.add_argument('--acknowledge-board', choices=['ZYBO_Z7_20'])
    p.add_argument('--uart-port', help='Optional; omit when root session already owns UART capture')
    p.add_argument('--initialize', action='store_true', help='Compatibility flag; every job resets PS, programs pinned FP32 bit, and initializes the same-XSA PS')
    p.add_argument('--timeout', type=int, default=90, help='Finite XSCT continue timeout seconds')
    p.add_argument('--firmware-timeout', type=int, choices=range(1, 61), default=60)
    p.add_argument('--max-polls', type=int, default=100000000)
    p.add_argument('--warmups', type=int, default=3, help='Discarded fresh-start trials, not persistent warm-cache trials')
    p.add_argument('--repeats', type=int, default=30)
    p.add_argument('--skip-timing', action='store_true')
    p.add_argument('--smoke-only', action='store_true')
    args = p.parse_args(argv)
    root = args.output_root.resolve()
    if not root.is_relative_to((BUILD/'board_validation').resolve()):
        p.error('Output must remain under build/board_validation')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.run_id) or args.run_id in ('.', '..'):
        p.error('run-id must be a new directory name')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.arm_run):
        p.error('arm-run must be a build/arm_fp32_accel directory name')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.system_run):
        p.error('system-run must be a build/system_fp32 directory name')
    if not 1 <= args.max_polls <= 0xffffffff or not 0 <= args.warmups <= 100 or not 1 <= args.repeats <= 100:
        p.error('Invalid finite poll/trial count')
    if not args.firmware_timeout < args.timeout <= 300:
        p.error('XSCT timeout must exceed firmware timeout and be at most 300 seconds')
    if args.execute and not all((args.target_filter, args.fpga_target_filter, args.cable_serial, args.acknowledge_board)):
        p.error('Execute requires observed CPU/FPGA filters, cable and --acknowledge-board ZYBO_Z7_20')
    out = root/args.run_id
    if out.exists():
        p.error('Refusing to overwrite evidence: '+str(out))
    out.mkdir(parents=True)
    state = dict(status='PREPARING', started_at_utc=datetime.now(timezone.utc).isoformat(),
                 argv=sys.argv, board_executed=False, timing_measured=False,
                 numeric_accuracy_status='DEVELOPMENT_ONLY_NOT_ALL_CASES_ACCEPTED',
                 known_synthetic_failures=references.KNOWN_SYNTHETIC_FAILURES,
                 synthetic_cases_retested=False, evaluation_audio_read=False)
    capture = None
    try:
        shutil.copyfile(__file__, out/'runner_snapshot.py')
        dump(out/'runner_dependencies.json', {str(path): sha(path) for path in
             (Path(__file__), PROJECT/'verification/arm/jtag.py', PROJECT/'verification/arm/clocks.py', PROJECT/'verification/arm/uart.py',
              PROJECT/'verification/arm_fp32_accel/references.py')})
        dependency_snapshots=out/'runner_dependencies'
        dependency_snapshots.mkdir()
        for path in (PROJECT/'verification/arm/jtag.py', PROJECT/'verification/arm/clocks.py', PROJECT/'verification/arm/uart.py',
                     PROJECT/'verification/arm_fp32_accel/references.py'):
            shutil.copyfile(path, dependency_snapshots/path.name)
        ident, pcm, reference, bundle = identity(out, BUILD/'arm_fp32_accel'/args.arm_run, BUILD/'system_fp32'/args.system_run)
        state['arm_run'] = args.arm_run
        state['system_run'] = args.system_run
        dump(out/'offline_checks.json', offline_checks(pcm, reference))
        state['preparation_passed'] = True
        state['timing_boundary'] = BOUNDARY
        state['startup_policy'] = STARTUP_POLICY
        state['hardware_selection'] = {key: getattr(args, key) for key in
                                      ('url', 'target_filter', 'fpga_target_filter', 'cable_serial', 'uart_port', 'acknowledge_board', 'initialize')}
        if not args.execute:
            args.target_filter = args.target_filter or 'name == "Cortex-A9 #0" && jtag_cable_serial == "OBSERVED_SERIAL"'
            args.cable_serial = args.cable_serial or 'OBSERVED_SERIAL'
            args.fpga_target_filter = args.fpga_target_filter or 'name =~ "xc7z020*" && jtag_cable_serial == "OBSERVED_SERIAL"'
            template = out/'templates'
            template.mkdir()
            for name, script in [('prepare', prepare_tcl(args, ident, template, args.initialize)),
                                 ('input', input_tcl(args, ident, template, 85920, 1)),
                                 ('execute', execute_tcl(args, ident, template, 6942))]:
                (template/(name+'.tcl')).write_text('# OFFLINE TEMPLATE: paths/identity must be selected by runner.\n'+script, encoding='utf-8')
            state['status'] = 'PREPARED_NOT_BOARD_RUN'
            return 0
        require(args.xsct.is_file(), 'XSCT missing')
        state['xsct'] = dict(path=str(args.xsct), sha256=sha(args.xsct))
        if args.uart_port:
            capture = Capture(args.uart_port, out)
            capture.start()
        state['board_execution_attempted'] = True
        smoke = job(args, ident, pcm[:1024], reference[:312], 0, out/'smoke', capture, bundle, args.initialize)
        state.update(board_executed=True, smoke=smoke)
        if args.smoke_only:
            state['status'] = 'SMOKE_PASS_ONLY_1_FRAME'
            return 0
        validation = job(args, ident, pcm, reference, 1, out/'development', capture, bundle)
        state['development'] = validation
        state['development_speech_numerical_passed'] = True
        if not args.skip_timing:
            trials = []
            for trial in range(args.warmups+args.repeats):
                discarded = trial < args.warmups
                result = job(args, ident, pcm, reference, 1, out/'timing'/f'trial_{trial:03d}', capture, bundle)
                result['discarded'] = discarded
                trials.append(result)
                dump(out/'timing_trials.json', trials)
            measured = [x for x in trials if not x['discarded']]
            seconds = [x['elapsed_seconds'] for x in measured]
            busy = [x['hardware_busy_seconds'] for x in measured]
            ordered = sorted(seconds)
            p95_index = (len(ordered)-1)*0.95
            p95_lower = int(p95_index)
            p95 = ordered[p95_lower]+(ordered[min(p95_lower+1, len(ordered)-1)]-ordered[p95_lower])*(p95_index-p95_lower)
            timing = dict(measured=True, discarded_fresh_starts=args.warmups, measured_fresh_starts=args.repeats,
                          scope=BOUNDARY, median_seconds=statistics.median(seconds), mean_seconds=statistics.mean(seconds),
                          min_seconds=min(seconds), max_seconds=max(seconds),
                          p95_seconds=p95, percentile_method='linear interpolation at (n-1)*0.95, matching NumPy default',
                          median_seconds_per_frame=statistics.median(seconds)/534,
                          median_hardware_busy_seconds=statistics.median(busy),
                          all_trial_outputs_bit_exact=True, trials=trials)
            dump(out/'timing_summary.json', timing)
            state['timing_measured'] = True
        state['status'] = 'BOARD_DEVELOPMENT_BIT_EXACT_PASS'
        return 0
    except Exception as error:
        state.update(status='FAILED', error=str(error), traceback=traceback.format_exc())
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if capture:
            capture.close()
        state['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        dump(out/'run_manifest.json', state)
        dump(out/'artifact_hashes.json', {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob('*'))
                                        if p.is_file() and p.name != 'artifact_hashes.json'})


if __name__ == '__main__':
    raise SystemExit(main())
