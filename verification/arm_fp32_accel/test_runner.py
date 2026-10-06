"""Offline FP32 runner/decoder/Tcl guard tests; uses plain tclsh, never XSCT."""
from argparse import ArgumentParser, Namespace
import importlib.util
import json
from pathlib import Path
import struct
import sys
import zlib

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fp32_board_test_target', PROJECT/'scripts/run_board_fp32_accel.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    checks = []
    bundle = runner.references.load()
    fixture = runner.offline_checks(bundle['pcm'], bundle['records'])
    assert fixture['passed']
    checks.append('runner_comparator_corruption_rejection_'+str(fixture['checks'])+'_checks')
    assert runner.references.numeric(bundle['records'], bundle)['passed']
    checks.append('frozen_development_python_and_pc_tolerances')
    altered = bytearray(bundle['records'])
    for label, value in [('nan', 0x7fc00001), ('infinity', 0x7f800000), ('out_of_tolerance', 0x42c80000)]:
        struct.pack_into('<Q', altered, 0, value)
        assert not runner.references.numeric(altered, bundle)['passed'], label
        checks.append('reject_numeric_'+label)
    # Explicitly verify offset124 is decoded as the full first-fault vector.
    status = bytearray(128)
    struct.pack_into('<I', status, 124, 0xabc)
    assert runner.decode_status(status)['transport']['core_error_detail'] == 0xabc
    checks.append('core_error_detail_status_offset124')
    # Exercise the actual job orchestration with file-producing Python fakes.
    # This never invokes the generated Tcl; failures must retain comparison
    # evidence and raise before a caller can enter the timing loop.
    saved_script, saved_clocks = runner.run_script, runner.check_clocks
    env = dict(cpu_registers=dict(sctlr=0x08c5187d, fpscr=0), l2_cache_control=1,
               global_timer_control=1, pl_configured_hz=100000000.0)
    runner.check_clocks = lambda *a: env.copy()
    names = ['arm_fp32_accel_ready_breakpoint', 'arm_fp32_accel_result_breakpoint',
             'arm_fp32_accel_status', 'arm_fp32_accel_layout', 'arm_fp32_accel_control',
             'arm_fp32_accel_results', 'arm_fp32_accel_pcm', 'MMUTable']
    ident = dict(symbols={name: dict(address=0x100000+i*0x4000, bytes=64) for i, name in enumerate(names)},
                 ps7_init='MOCK_ONLY_ps7_init.tcl', bit='MOCK_ONLY.bit', elf='MOCK_ONLY.elf')
    jobargs = Namespace(url='OFFLINE_MOCK_ONLY', target_filter='CPU0_ONLY', cable_serial='MOCK_ONLY',
                        fpga_target_filter='FPGA_ONLY', timeout=90, firmware_timeout=60, max_polls=1000000)
    for fault in ('ok', 'crc', 'metadata', 'core_detail', 'poll_limit', 'timer_deadline', 'python_tolerance'):
        destination = args.output/('job_'+fault)
        def fake_script(arguments, script, path):
            folder = path.parent
            if path.stem == 'prepare':
                values = (runner.LAYOUT[0], 1, 1, 0, 0, 0, 0xffffffff, 0, 333333343)
                (folder/'ready_status.bin').write_bytes(struct.pack('<IIIiIIIIQ', *values)+bytes(88))
                (folder/'layout.bin').write_bytes(struct.pack('<16I', *runner.LAYOUT))
            elif path.stem == 'input':
                (folder/'pcm_readback.bin').write_bytes(bundle['pcm'])
            elif path.stem == 'execute':
                crc = zlib.crc32(bundle['pcm']) ^ int(fault == 'crc')
                values = (runner.LAYOUT[0], 1, 3, 0, 0, 0, 0xffffffff, crc, 333333343)
                transport = dict.fromkeys(runner.STAT_NAMES, 0)
                transport.update(polls=100000, samples_sent=85920, records_received=6942, expected_frames=534,
                                 status=2, input_written=85920, input_consumed=85920, output_captured=6942,
                                 output_popped=6942, last_lo=bundle['bits'][-1], last_frame=533,
                                 last_meta=0x1000c, failed_offset=0xffffffff)
                if fault == 'core_detail': transport['core_error_detail'] = 0xabc
                if fault == 'poll_limit': transport['polls'] = jobargs.max_polls+1
                ticks = 333333343*60 if fault == 'timer_deadline' else 100000
                (folder/'result_status.bin').write_bytes(struct.pack('<IIIiIIIIQ', *values)+
                    struct.pack('<QQ18I', ticks, 20000, *(transport[k] for k in runner.STAT_NAMES)))
                output = bytearray(bundle['records'])
                if fault == 'metadata': output[16] = 1
                (folder/'results.bin').write_bytes(output)
            else:
                raise AssertionError('Unexpected fake script phase')
        runner.run_script = fake_script
        numeric_bundle = bundle
        if fault == 'python_tolerance':
            numeric_bundle = {**bundle, 'python': [v+1.0 for v in bundle['python']]}
        try:
            value = runner.job(jobargs, ident, bundle['pcm'], bundle['records'], 1, destination, None, numeric_bundle)
        except ValueError:
            assert fault != 'ok', fault
            assert not runner.read(destination/'comparison.json')['passed']
        else:
            assert fault == 'ok' and value['passed'], fault
        checks.append('job_gate_'+fault)
    runner.run_script, runner.check_clocks = saved_script, saved_clocks
    tclsh = Path('C:/Xilinx/Vitis/2024.2/tps/win64/git-2.45.0/mingw64/bin/tclsh.exe')
    for label in ('ok', 'already_stopped', 'ambiguous_cpu0', 'wrong_cpu0', 'wrong_cable',
                  'ambiguous_cpu1', 'stop_error', 'wrong_pc', 'unconfigured_fpga', 'ambiguous_fpga'):
        destination = args.output/('tcl_'+label)
        destination.mkdir()
        mock = f'''set fault {label}
set selected 0
set current_pc 0
proc connect {{args}} {{ puts "MOCK_CONNECT" }}
proc targets {{args}} {{
 if {{[lindex $args 0] eq "-set"}} {{set ::selected [lindex $args 1]; puts "MOCK_SELECT $::selected"; return}}
 set serial MOCK_ONLY
 if {{$::fault eq "wrong_cable"}} {{set serial OTHER}}
 set name {{Cortex-A9 #0}}
 if {{$::fault eq "wrong_cpu0"}} {{set name {{Cortex-A9 #1}}}}
 set one [dict create name $name jtag_cable_serial $serial target_id 2]
 set two [dict create name {{Cortex-A9 #1}} jtag_cable_serial MOCK_ONLY target_id 3]
 set fpga [dict create name xc7z020 jtag_cable_serial MOCK_ONLY target_id 4]
 if {{[llength $args] == 1}} {{
  if {{$::fault eq "ambiguous_cpu1"}} {{return [list $one $two $two]}}
  return [list $one $two]
 }}
 if {{[lindex $args end] eq "FPGA_ONLY"}} {{
  if {{$::fault eq "ambiguous_fpga"}} {{return [list $fpga $fpga]}}
  return [list $fpga]
 }}
 if {{$::fault eq "ambiguous_cpu0"}} {{return [list $one $one]}}
 return [list $one]
}}
proc stop {{args}} {{
 puts "MOCK_STOP $::selected"
 if {{$::fault eq "already_stopped"}} {{error "Already stopped"}}
 if {{$::fault eq "stop_error"}} {{error "Transport failed"}}
}}
proc rst {{args}} {{puts "MOCK_RESET $args"}}
proc source {{args}} {{puts "MOCK_SOURCE"}}
proc ps7_init {{args}} {{puts "MOCK_PS_INIT"}}
proc ps7_post_config {{args}} {{puts "MOCK_PS_POST"}}
proc fpga {{args}} {{
 if {{[lindex $args 0] eq "-file"}} {{puts "MOCK_FPGA_PROGRAM $::selected"; return}}
 if {{[lindex $args 0] eq "-state"}} {{
  if {{$::fault eq "unconfigured_fpga"}} {{return "FPGA is not configured"}}
  return "FPGA is configured"
 }}
 return "MOCK_CONFIG_STATUS"
}}
proc dow {{args}} {{puts "MOCK_DOWNLOAD $::selected"}}
proc mrd {{args}} {{return 0}}
proc bpadd {{args}} {{set ::current_pc [lindex $args 1]; return 1}}
proc bpremove {{args}} {{}}
proc con {{args}} {{puts "MOCK_CONTINUE $::selected"}}
proc rrd {{args}} {{
 set name [lindex $args end]
 if {{$name eq "pc"}} {{
  set value $::current_pc
  if {{$::fault eq "wrong_pc"}} {{incr value}}
  return [dict create pc [format %08x $value]]
 }}
 return [dict create $name 00000000]
}}
'''
        execution = Namespace(url='OFFLINE_MOCK_ONLY', target_filter='CPU0_ONLY', cable_serial='MOCK_ONLY',
                              fpga_target_filter='FPGA_ONLY', timeout=1, xsct=tclsh)
        expected_success = label in ('ok', 'already_stopped')
        try:
            runner.run_script(execution, mock+runner.prepare_tcl(execution, ident, destination), destination/'mock.tcl')
        except ValueError:
            assert not expected_success, label
        else:
            assert expected_success, label
            log = (destination/'mock.log').read_text()
            assert log.count('MOCK_CONNECT') == 1
            order = ['MOCK_RESET -system -stop', 'MOCK_STOP 3', 'MOCK_STOP 2',
                     'MOCK_PS_INIT', 'MOCK_FPGA_PROGRAM 4', 'MOCK_DOWNLOAD 2', 'MOCK_CONTINUE 2']
            positions = [log.index(v) for v in order]
            assert positions == sorted(positions), label
            # A second post_config is required after reprogramming the PL.
            assert log.index('MOCK_FPGA_PROGRAM 4') < log.rindex('MOCK_PS_POST') < log.index('MOCK_DOWNLOAD 2')
        checks.append('tcl_'+label)
    # Guards are also present on reconnects; these scripts cannot reset/program.
    dummy = Namespace(url='OFFLINE_MOCK_ONLY', target_filter='CPU0_ONLY', cable_serial='MOCK_ONLY', timeout=1)
    for name, text in [('input', runner.input_tcl(dummy, ident, args.output, 85920, 1)),
                       ('execute', runner.execute_tcl(dummy, ident, args.output, 6942))]:
        assert 'Lost READY state' in text and 'CPU did not stop at expected breakpoint' in text
        assert 'rst ' not in text and 'fpga -file' not in text
        checks.append(name+'_reconnect_ready_pc_guard_and_no_reset')
    result = dict(passed=True, actual_board_access=False, actual_xsct_invoked=False,
                  fixture_kind='Synthetic host and plain-tclsh mocks, not hardware evidence', checks=checks,
                  comparator_checks=fixture['checks'], reference=bundle['metadata'])
    runner.dump(args.output/'report.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
