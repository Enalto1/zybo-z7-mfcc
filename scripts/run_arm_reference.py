"""Prepare or explicitly execute the standalone ZYBO Z7-20 ARM MFCC experiment.

Default/--prepare-only is offline: artifact integrity, ELF symbol and protocol
checks plus a reproducible plan. It cannot satisfy the ARM development gate.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import subprocess
import sys
from pathlib import Path
import traceback
import numpy as np
sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'verification/arm'))
from preparation import prepare,read,dump,sha
import protocol
import jtag
from clocks import expected_configuration,verify_configuration
from comparison import compare_results,trace_comparison,choose_focus_frames,development_gate
from uart import Capture


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot():
    files=[Path(__file__)]
    for group in ('verification/arm','software/arm','software/c','verification/c'):
        files += [p for p in (PROJECT/group).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.c','.h','.tcl')]
    return {p.relative_to(PROJECT).as_posix():sha(p) for p in sorted(files)}


def process(argv,out,timeout=180):
    try:
        result=subprocess.run([str(x) for x in argv],capture_output=True,text=True,errors='replace',timeout=timeout)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
        out.write_text('ARGV '+json.dumps([str(x) for x in argv])+'\n'+decoded(error.stdout)+'\n'+decoded(error.stderr)+f'\nTIMEOUT {timeout}\n',encoding='utf-8')
        raise
    out.write_text('ARGV '+json.dumps([str(x) for x in argv])+'\n'+result.stdout+'\n'+result.stderr+f'\nEXIT {result.returncode}\n',encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Command failed: '+str(out))
    return result.stdout


def build_identity(path: Path, args, out: Path) -> dict:
    build=read(path)
    if build.get('status') != 'built_not_board_verified':
        raise ValueError('ARM application build is not successful: '+str(build.get('status')))
    for relative,digest in build['source_hashes'].items():
        if sha(PROJECT/relative)!=digest:
            raise ValueError('Application build source changed: '+relative)
    pc=Path(read(PROJECT/'verification/arm/baseline_pins.json')['c_root'])
    for name,digest in build['coefficient_hashes'].items():
        if sha(pc/'coefficients'/name)!=digest:
            raise ValueError('Application coefficient differs from frozen PC baseline: '+name)
    for key in ('xsa','compiler','libm'):
        if sha(Path(build[key]['path']))!=build[key]['sha256']:
            raise ValueError('ARM build dependency changed: '+key)
    bsp=build['bsp']
    if sha(Path(bsp['include'])/'xparameters.h')!=bsp['xparameters_sha256'] or sha(Path(bsp['lib'])/'libxil.a')!=bsp['libxil_sha256']:
        raise ValueError('ARM BSP differs from compiled identity')
    for relative,digest in read(path.parent/'artifact_manifest.json').items():
        target=(path.parent/relative).resolve()
        if not target.is_relative_to(path.parent.resolve()) or sha(target)!=digest:
            raise ValueError('Application build artifact changed: '+relative)
    nm=Path(args.nm or build.get('toolchain',{}).get('nm',''))
    if not nm.is_file():
        raise ValueError('Provide --nm path to the pinned ARM toolchain nm executable')
    binaries={}
    for label in ('hello_ddr','mfcc_arm'):
        record=build['binaries'][label]
        elf=Path(record['path'])
        if sha(elf)!=record['sha256']:
            raise ValueError('ARM ELF hash differs: '+label)
        raw=process([nm,'-n',elf],out/(label+'_nm.log'))
        binaries[label]=dict(path=str(elf),sha256=sha(elf),symbols=protocol.parse_nm(raw,hello=label=='hello_ddr'))
    init=Path(args.ps7_init or build.get('ps7_init',{}).get('path',''))
    if not init.is_file():
        raise ValueError('Provide generated --ps7-init corresponding to the pinned XSA')
    if build.get('ps7_init',{}).get('sha256') and sha(init)!=build['ps7_init']['sha256']:
        raise ValueError('PS7 initialization script hash differs')
    # The init file must be a byte-exact member of this XSA, not another board.
    import zipfile,hashlib
    with zipfile.ZipFile(build['xsa']['path']) as archive:
        matches=[name for name in archive.namelist() if Path(name).name=='ps7_init.tcl']
        if len(matches)!=1 or hashlib.sha256(archive.read(matches[0])).hexdigest()!=sha(init):
            raise ValueError('PS7 init is not the script exported in the pinned XSA')
    xsct=Path(args.xsct or build.get('toolchain',{}).get('xsct',''))
    if not xsct.is_file():
        raise ValueError('Provide installed --xsct path')
    return dict(build_manifest=str(path),build_manifest_sha256=sha(path),build=build,binaries=binaries,
        nm=dict(path=str(nm),sha256=sha(nm)),ps7_init=dict(path=str(init),sha256=sha(init)),
        xsct=dict(path=str(xsct),sha256=sha(xsct)),
        expected_clocks=expected_configuration(Path(build['xsa']['path']),init))


def run_tcl(config, script: str, path: Path):
    sentinel='ARM_RUN_TCL_COMPLETED_SUCCESSFULLY'
    # XSCT 2024.2 can exit zero after an uncaught Tcl script error. A successful
    # process alone is therefore insufficient evidence that every command ran.
    wrapped='if {[catch {\n'+script+'''} script_error script_options]} {
    puts stderr "ARM_RUN_TCL_FAILED: $script_error"
    if {[dict exists $script_options -errorinfo]} {puts stderr [dict get $script_options -errorinfo]}
    exit 1
}
'''+f'puts "{sentinel}"\nexit 0\n'
    path.write_text(wrapped,encoding='utf-8')
    stdout=process([config['xsct'],path],path.with_suffix('.log'),timeout=config['timeout']+60)
    if stdout.splitlines().count(sentinel)!=1:
        raise RuntimeError('XSCT completion sentinel missing or duplicated: '+str(path.with_suffix('.log')))


def hello(config,identity,out,capture):
    dest=out/'hello';dest.mkdir()
    binary=identity['binaries']['hello_ddr']
    offset=capture.marker()
    run_tcl(config,jtag.prepare_job(config,binary['symbols'],Path(binary['path']),None,dest,initialize=True),dest/'prepare.tcl')
    capture.require('ARM_HELLO_DDR_V1',offset)
    status=protocol.decode_status((dest/'ready_status.bin').read_bytes())
    protocol.decode_layout((dest/'layout.bin').read_bytes())
    clocks=verify_configuration((dest/'clock_registers.bin').read_bytes(),identity['expected_clocks'],status)
    dump(dest/'validation.json',dict(passed=True,uart_marker_observed=True,ddr_test_bytes=4096,status=status,clocks=clocks))


def job(config,identity,case,dest,capture,*,mode=1,frame=0,warmups=0,repeats=0,validated=False):
    dest.mkdir(parents=True)
    binary=identity['binaries']['mfcc_arm'];symbols=binary['symbols'];pcm=Path(case['pcm'])
    offset=capture.marker()
    run_tcl(config,jtag.prepare_job(config,symbols,Path(binary['path']),pcm,dest),dest/'prepare.tcl')
    capture.require('ARM_MFCC_READY_V1',offset)
    ready=protocol.decode_status((dest/'ready_status.bin').read_bytes())
    layout=protocol.decode_layout((dest/'layout.bin').read_bytes())
    clocks=verify_configuration((dest/'clock_registers.bin').read_bytes(),identity['expected_clocks'],ready)
    if sha(dest/'pcm_readback.bin')!=case['pcm_sha256']:
        raise ValueError('Full target PCM readback SHA256 differs; RUN command was not written')
    (dest/'control.bin').write_bytes(protocol.control(case['sample_count'],case['pcm_crc32'],mode=mode,
        trace_frame=frame,warmups=warmups,repeats=repeats,validation_passed=validated))
    run_tcl(config,jtag.execute_job(config,symbols,dest,layout,case['sample_count'],case['frame_count'],mode,repeats),dest/'execute.tcl')
    status=protocol.decode_status((dest/'result_status.bin').read_bytes(),done=True,samples=case['sample_count'],expected_crc=case['pcm_crc32'])
    if status['mode']!=mode:
        raise ValueError('Firmware reported unexpected mode')
    if status['cpu_hz']!=ready['cpu_hz'] or status['timer_hz']!=ready['timer_hz']:
        raise ValueError('Clock status changed within a job')
    result=dict(status=status,ready_status=ready,layout=layout,clocks=clocks,input_readback_sha256=sha(dest/'pcm_readback.bin'),
        input_crc32=case['pcm_crc32'],structural_passed=True,actual_board_executed=True)
    if mode==3:
        result['timing']=protocol.timing_statistics((dest/'timing_records.bin').read_bytes(),status,case['frame_count'])
        return result
    records=protocol.decode_results((dest/'results.bin').read_bytes(),case['sample_count'])
    if protocol.result_checksum(records)!=status['output_checksum']:
        raise ValueError('Output record checksum differs from ARM status')
    result['comparison']=compare_results(records,case,config['tolerance'])
    if mode==2:
        if status['trace_valid']!=int(case['frame_count']>0) or status['trace_frame']!=frame:
            raise ValueError('Firmware trace is invalid or refers to another frame')
        stages=protocol.decode_trace((dest/'trace.bin').read_bytes(),layout,frame) if case['frame_count'] else {}
        pre=np.fromfile(dest/'preemphasis.bin',dtype='<f4')
        if pre.shape!=(case['sample_count'],) or not np.isfinite(pre).all():
            raise ValueError('Preemphasis readback shape/finite check failed')
        result['trace_comparison']=trace_comparison(stages,pre,case,frame,config['tolerance'])
    dump(dest/'validation.json',result)
    return result,records


def run_case(config,identity,case,out,capture):
    dest=out/case['role']/case['id']
    result,records=job(config,identity,case,dest/'output',capture)
    traces=[]
    for frame in choose_focus_frames(records,case):
        trace,_=job(config,identity,case,dest/f'trace_{frame:06d}',capture,mode=2,frame=frame)
        # Reset/re-run itself must reproduce the output exactly.
        if (dest/'output/results.bin').read_bytes()!=(dest/f'trace_{frame:06d}'/'results.bin').read_bytes():
            raise ValueError('Repeated clip output differs between validation and trace mode')
        traces.append(trace['trace_comparison'])
    record={**case,**result,'traces':traces}
    dump(dest/'validation.json',record)
    print(f'{case["role"]}: {case["id"]}: Python={result["comparison"]["python"]["passed"]} PC={result["comparison"]["pc"]["passed"]}',flush=True)
    return record


def run_timing(config,identity,case,out,capture,warmups,repeats,prior_checksum):
    result=job(config,identity,case,out,capture,mode=3,warmups=warmups,repeats=repeats,validated=True)
    if result['status']['output_checksum']!=prior_checksum:
        raise ValueError('Timed output checksum differs from validated clip')
    if result['status']['warmups_done']!=warmups or result['status']['repeats_done']!=repeats:
        raise ValueError('Firmware timing counts differ from frozen parameters')
    dump(out/'validation.json',result)
    return dict(id=case['id'],role=case['role'],timing=result['timing'],status=result['status'])


def speech_gate(record):
    """Accept only the frozen development speech, without opening evaluation."""
    reasons=[]
    if (record['role']!='development' or record['id']!='8463-294828-0037'
            or record['sample_count']!=85920 or record['frame_count']!=534
            or record['pcm_sha256']!='026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32'):
        reasons.append('Speech scope requires the frozen development PCM / 85920 samples / 534 frames')
    if not record.get('actual_board_executed') or not record.get('structural_passed'):
        reasons.append('Actual board execution and all structural checks are required')
    if not all(record['comparison'][label]['passed'] for label in ('python','pc')):
        reasons.append('Development speech final outputs must pass both Python and PC tolerances')
    if (not record['traces'] or not any(t['frame']==0 for t in record['traces'])
            or not all(t[label]['passed'] for t in record['traces'] for label in ('python','pc'))):
        reasons.append('All selected focus traces must pass both Python and PC tolerances')
    return dict(passed=not reasons,reasons=reasons,scope='development_speech_only',
        development_gate_passed=False,controlled_evaluation_allowed=False,
        intermediate_coverage='first and earliest/worst output-difference frames; not every intermediate frame')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--prepare-only',action='store_true')
    action.add_argument('--execute',action='store_true')
    parser.add_argument('--dry-run',action='store_true',help='Alias for offline preparation')
    parser.add_argument('--build-manifest',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,default=PROJECT.parent/'build/arm_platform')
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--phase',choices=['develop','all','evaluate','speech'],default='all',
        help='speech validates/times only the frozen development clip; it cannot satisfy the full development gate')
    parser.add_argument('--development-run',type=Path)
    parser.add_argument('--nm');parser.add_argument('--xsct');parser.add_argument('--ps7-init')
    parser.add_argument('--target-filter');parser.add_argument('--cable-serial')
    parser.add_argument('--acknowledge-board',choices=['ZYBO_Z7_20'])
    parser.add_argument('--uart-port');parser.add_argument('--url',default='TCP:127.0.0.1:3121')
    parser.add_argument('--timeout',type=int,default=60)
    parser.add_argument('--hardware-probe-log',type=Path)
    parser.add_argument('--timing',action='store_true',default=True,help='Timing is enabled after actual numerical gates')
    parser.add_argument('--skip-timing',action='store_true',help='Diagnostic run; frozen as a distinct experiment')
    parser.add_argument('--warmups',type=int,choices=[3],default=3)
    parser.add_argument('--repeats',type=int,choices=[30],default=30)
    args=parser.parse_args(argv)
    if args.dry_run and args.execute:
        parser.error('--dry-run and --execute are incompatible')
    if not args.run_id or Path(args.run_id).name!=args.run_id or args.run_id in ('.','..'):
        parser.error('run-id must be a single new directory name')
    root=args.output_root.resolve()
    if not any(root.is_relative_to((PROJECT.parent/'build'/name).resolve()) for name in ('arm_platform','board_validation')):
        parser.error('Outputs must remain under build/arm_platform or build/board_validation')
    out=root/args.run_id
    if out.exists():
        parser.error('Refusing to replace existing evidence directory')
    out.mkdir(parents=True)
    manifest=dict(started_at_utc=now(),status='running',board_executed=False,command_argv=sys.argv,
        output=str(out),phase=args.phase,development_gate_passed=False,evaluation_executed=False,timing_measured=False)
    capture=None
    try:
        plan=prepare(PROJECT);dump(out/'plan.json',plan)
        if args.phase=='speech':
            limits=read(PROJECT/'verification/c/known_development_limits.json')
            retained=[dict(id=name,failed_stages=stages,baseline_numerical_passed=False,
                board_status='not_retested',observed_in=limits['observed_in'])
                for name,stages in limits['allowed_numerical_failure_stages'].items()]
            scope=dict(name='development_speech_only',sample_count=85920,frame_count=534,mfcc_count=6942,
                synthetic_status='not_retested',evaluation_status='not_run',controlled_evaluation_allowed=False,
                retained_baseline_failures=retained)
            dump(out/'scope.json',scope)
            manifest.update(scope=scope,speech_numerical_passed=False,numeric_acceptance_passed=None,
                controlled_evaluation_allowed=False,retained_baseline_failures=retained)
        identity=build_identity(args.build_manifest.resolve(),args,out);dump(out/'build_identity.json',identity)
        conditions=dict(source_hashes=snapshot(),build_manifest_sha256=identity['build_manifest_sha256'],
            baseline_pins=plan['pins'],tolerance_sha256=plan['tolerance_sha256'],known_limits_sha256=plan['known_limits_sha256'],
            nm=identity['nm'],ps7_init=identity['ps7_init'],xsct=identity['xsct'],expected_clocks=identity['expected_clocks'],
            hardware_selection=dict(url=args.url,target_filter=args.target_filter,cable_serial=args.cable_serial,
                uart_port=args.uart_port,acknowledge_board=args.acknowledge_board) if args.execute else None,
            timing=dict(enabled=not args.skip_timing,warmups=args.warmups,repeats=args.repeats,
                scope='mfcc_init plus input loop, frame checks and checksum; no JTAG/UART/CRC/output copies/cache operations'))
        dump(out/'conditions.json',conditions)
        if args.hardware_probe_log:
            manifest['hardware_probe']=dict(path=str(args.hardware_probe_log),sha256=sha(args.hardware_probe_log),
                interpretation='Discovery evidence only; does not prove ARM program execution')
        from test_protocol import run_tests
        tests=run_tests();dump(out/'offline_protocol_tests.json',tests)
        if not tests['passed']:
            raise ValueError('Offline protocol tests failed')
        manifest.update(preparation_passed=True,offline_protocol_tests_passed=True,conditions_sha256=sha(out/'conditions.json'))
        if not args.execute:
            template=dict(url=args.url,target_filter='name == "Cortex-A9 #0" && jtag_cable_serial == "OBSERVED_SERIAL"',
                cable_serial='OBSERVED_SERIAL',ps7_init=identity['ps7_init']['path'],timeout=args.timeout)
            first=next(c for c in plan['cases'] if c['role']=='development')
            script=jtag.prepare_job(template,identity['binaries']['mfcc_arm']['symbols'],
                Path(identity['binaries']['mfcc_arm']['path']),Path(first['pcm']),out,initialize=True)
            (out/'REVIEW_ONLY_prepare_template.tcl').write_text('# TEMPLATE ONLY: supply observed identity before execution.\n'+script,encoding='utf-8')
            manifest.update(status='prepared_unverified_on_arm',limitations=plan['limitations'])
            return 0
        if not all([args.target_filter,args.cable_serial,args.acknowledge_board,args.uart_port]):
            raise ValueError('Execute requires observed --target-filter, --cable-serial, --uart-port and --acknowledge-board ZYBO_Z7_20')
        xsct=Path(identity['xsct']['path'])
        config=dict(url=args.url,target_filter=args.target_filter,cable_serial=args.cable_serial,
            ps7_init=identity['ps7_init']['path'],timeout=args.timeout,xsct=str(xsct),
            tolerance=read(PROJECT/'verification/c/tolerances.json'))
        manifest['hardware_selection']={k:config[k] for k in ('url','target_filter','cable_serial')}
        manifest['xsct']=dict(path=str(xsct),sha256=sha(xsct))
        def unchanged():
            if snapshot()!=conditions['source_hashes'] or sha(args.build_manifest)!=conditions['build_manifest_sha256']:
                raise ValueError('Frozen sources/build identity changed during execution')
            for label in identity['binaries'].values():
                if sha(Path(label['path']))!=label['sha256']:
                    raise ValueError('Frozen ELF changed during execution')
            for tool in ('xsct','nm','ps7_init'):
                if sha(Path(identity[tool]['path']))!=identity[tool]['sha256']:
                    raise ValueError('Frozen tool/init changed during execution')
        capture=Capture(args.uart_port,out);capture.start()
        hello(config,identity,out,capture);manifest['board_executed']=True
        dev=[];timings=[]
        if args.phase=='speech':
            selected=[case for case in plan['cases'] if case['role']=='development']
            if len(selected)!=1:
                raise ValueError('Speech phase requires exactly one frozen development clip')
            unchanged();speech=run_case(config,identity,selected[0],out,capture)
            gate=speech_gate(speech);dump(out/'speech_gate.json',gate)
            manifest['speech_numerical_passed']=gate['passed']
            if not gate['passed']:
                manifest['status']='development_speech_failed_timing_blocked'
                return 2
            if not args.skip_timing:
                unchanged()
                timings.append(run_timing(config,identity,speech,out/'timing'/speech['id'],capture,
                    args.warmups,args.repeats,speech['status']['output_checksum']))
                manifest['timing_measured']=True
            dump(out/'timing_summary.json',dict(measured=bool(timings),clips=timings,scope=scope,
                known_synthetic_failures_excluded_from_timing=retained))
            # Deliberately no development_gate.json/freeze.json: this narrow run
            # cannot serve as the prerequisite evidence for --phase evaluate.
            manifest['status']='passed_development_speech_only'
            return 0
        if args.phase in ('develop','all'):
            for case in plan['cases']:
                if case['role']=='evaluation':continue
                unchanged();dev.append(run_case(config,identity,case,out,capture))
            gate=development_gate(dev,read(PROJECT/'verification/c/known_development_limits.json'))
            dump(out/'development_gate.json',gate)
            manifest['development_gate_passed']=gate['passed']
            if not gate['passed']:
                manifest['status']='development_failed_evaluation_blocked';return 2
            if not args.skip_timing:
                speech=next(x for x in dev if x['role']=='development')
                if not speech['comparison']['python']['passed'] or not speech['comparison']['pc']['passed']:
                    raise ValueError('Development speech timing requires actual numerical acceptance')
                unchanged()
                timings.append(run_timing(config,identity,speech,out/'timing'/speech['id'],capture,
                    args.warmups,args.repeats,speech['status']['output_checksum']))
                manifest['timing_measured']=True
            freeze=dict(frozen_at_utc=now(),conditions=conditions,development_gate_sha256=sha(out/'development_gate.json'),
                development_validations={str(Path(x['role'])/x['id']/'validation.json'):sha(out/x['role']/x['id']/'validation.json') for x in dev},
                development_timing={p.relative_to(out).as_posix():sha(p) for p in (out/'timing').rglob('*') if p.is_file()} if (out/'timing').exists() else {})
            dump(out/'freeze.json',freeze)
        else:
            if not args.development_run:
                raise ValueError('Evaluation requires an actual prior --development-run')
            prior=args.development_run;freeze=read(prior/'freeze.json');gate=read(prior/'development_gate.json')
            if not gate.get('passed') or freeze['conditions']!=conditions or sha(prior/'development_gate.json')!=freeze['development_gate_sha256']:
                raise ValueError('Prior development gate/frozen conditions do not match')
            for rel,digest in freeze['development_validations'].items():
                if sha(prior/rel)!=digest:raise ValueError('Prior development evidence changed')
            for rel,digest in freeze.get('development_timing',{}).items():
                if sha(prior/rel)!=digest:raise ValueError('Prior development timing changed')
            dump(out/'freeze.json',freeze);manifest['development_gate_passed']=True
        evaluations=[]
        if args.phase in ('evaluate','all'):
            manifest['evaluation_started_at_utc']=now()
            for case in plan['cases']:
                if case['role']!='evaluation':continue
                unchanged();record=run_case(config,identity,case,out,capture);evaluations.append(record)
                eligible=record['comparison']['python']['passed'] and record['comparison']['pc']['passed'] and all(
                    t['python']['passed'] and t['pc']['passed'] for t in record['traces'])
                if not args.skip_timing and eligible:
                    unchanged()
                    timings.append(run_timing(config,identity,case,out/'timing'/case['id'],capture,
                        args.warmups,args.repeats,record['status']['output_checksum']))
                    manifest['timing_measured']=True
            if len(evaluations)!=20 or sum(x['frame_count'] for x in evaluations)!=9501:
                raise ValueError('Evaluation must contain exactly 20 clips / 9501 frames')
            manifest['evaluation_executed']=True
        eval_pass=bool(evaluations) and all(x['comparison']['python']['passed'] and x['comparison']['pc']['passed']
            and all(t['python']['passed'] and t['pc']['passed'] for t in x['traces']) for x in evaluations)
        manifest['evaluation_numerical_passed']=eval_pass if evaluations else None
        dump(out/'timing_summary.json',dict(measured=bool(timings),clips=timings,
            known_synthetic_failures_excluded_from_timing=gate.get('retained_baseline_failures',[])))
        retained=gate.get('retained_baseline_failures',[])
        failed=bool(retained) or (bool(evaluations) and not eval_pass)
        manifest.update(status='completed_with_numerical_failures' if failed else 'passed',
            numeric_acceptance_passed=not failed,retained_baseline_failures=retained)
        return 2 if failed else 0
    except Exception as error:
        manifest.update(status='failed',error=str(error),traceback=traceback.format_exc())
        print(str(error),file=sys.stderr)
        return 1
    finally:
        if capture:capture.close()
        manifest['completed_at_utc']=now()
        dump(out/'run_manifest.json',manifest)
        dump(out/'artifact_manifest.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='artifact_manifest.json'})


if __name__=='__main__':
    raise SystemExit(main())
