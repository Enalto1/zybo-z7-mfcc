"""Freeze and verify a new ZYBO PS/PL fixed-MFCC system; never access a board.

Run only after the other FP32 simulation session has finished. The process guard
refuses to overlap an already running Vivado/XSim job. It is not a lock honored
by unrelated tools, so the operator must also coordinate session scheduling.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT.parent / 'build'
VIVADO = Path('C:/Xilinx/Vivado/2024.2/bin/vivado.bat')
GOLD = BUILD / 'fixed_full_rtl/full_616_repro_20261004_07'
MODEL = BUILD / 'fixed_full_model/integer_02_20261004'
CONTRACT = BUILD / 'fixed_contract/v2_pcm16_mfcc40_20261004_r2'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def process_guard():
    # Get-Process does not inspect other command lines or change their state.
    command = "Get-Process -Name vivado,xsim,xsimk,xelab,xvlog,xvhdl -ErrorAction SilentlyContinue | Select-Object Id,ProcessName | ConvertTo-Json -Compress; exit 0"
    process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError('Cannot inspect competing Vivado jobs: '+process.stderr)
    result = process.stdout.strip()
    if result and result != 'null':
        raise RuntimeError('Other Vivado/XSim job is active; defer this run: '+result)
    return dict(checked_at_utc=now(), active_jobs=[])


def stage_inputs(out, smoke):
    model = json.loads((MODEL/'run_manifest.json').read_text())
    contract = json.loads((CONTRACT/'contract.json').read_text())
    if model['status'] != 'complete' or model['total_frames'] != 616:
        raise ValueError('Incomplete integer model baseline')
    if sha(MODEL/'run_manifest.json') != contract['source_run']['sha256']:
        raise ValueError('Pinned contract/model identity mismatch')
    if sha(CONTRACT/'contract.json') != '283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e':
        raise ValueError('Immutable numeric contract changed')
    chosen = []
    with (out/'pcm.mem').open('w') as pcm_stream, (out/'mfcc.mem').open('w') as mfcc_stream, (out/'shift.mem').open('w') as shift_stream:
        for case in model['cases']:
            if smoke and case['group'] != 'development' and not case['id'].startswith('boundary'):
                continue
            pcm_path = MODEL/'arrays'/case['pcm']['file']
            integers = MODEL/case['integer_file']
            if sha(pcm_path) != case['pcm']['sha256'] or sha(integers) != case['sha256']:
                raise ValueError('Corrupt input/reference: '+case['id'])
            pcm = np.fromfile(pcm_path, dtype='<i2')
            frames = case['frames']
            if smoke and case['group'] == 'development':
                pcm, frames = pcm[:832], 3
            pcm_stream.write(''.join(f'{int(x)&65535:04x}\n' for x in pcm))
            with np.load(integers, allow_pickle=False) as values:
                mfcc_stream.write(''.join(f'{int(x)&((1<<40)-1):010x}\n' for x in values['mfcc_q24'][:frames].ravel()))
                shift_stream.write(''.join(f'{int(x)&255:02x}\n' for x in values['shift_s'][:frames].ravel()))
                if case['group'] == 'development':
                    (out/'ps_pcm.mem').write_text(''.join(f'{int(x)&65535:04x}\n' for x in pcm[:672]))
                    (out/'ps_mfcc.mem').write_text(''.join(f'{int(x)&((1<<40)-1):010x}\n' for x in values['mfcc_q24'][:2].ravel()))
                    (out/'ps_shift.mem').write_text(''.join(f'{int(x)&255:02x}\n' for x in values['shift_s'][:2].ravel()))
            chosen.append(dict(id=case['id'], group=case['group'], samples=len(pcm), frames=frames,
                               pcm_sha256=case['pcm']['sha256'], integer_sha256=case['sha256']))
    total = sum(c['frames'] for c in chosen)
    (out/'cases.txt').write_text(f'{len(chosen)} {total}\n'+''.join(f'{c["samples"]} {c["frames"]}\n' for c in chosen))
    return chosen


def snapshot(out):
    gold = json.loads((GOLD/'run_manifest.json').read_text())
    sources = sorted((ROOT/'hardware/fixed').rglob('*.sv')) + sorted((ROOT/'hardware/fixed').rglob('*.mem'))
    sources += sorted((ROOT/'hardware/system').glob('*')) + sorted((ROOT/'verification/system').glob('*.sv'))
    sources += [Path(__file__), ROOT/'hardware/fixed/fft/PROVENANCE.json', ROOT/'hardware/platform/board_source.json', ROOT/'AGENTS.md', ROOT/'docs/RTL_CODING_RULES.md']
    hashes = {}
    for source in sources:
        if not source.is_file():
            continue
        relative = source.relative_to(ROOT)
        digest = sha(source)
        if relative.as_posix().startswith('hardware/fixed/'):
            if gold['source_sha256'].get(relative.as_posix()) != digest:
                raise ValueError('Verified fixed core changed: '+str(relative))
        destination = out/'source'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[relative.as_posix()] = digest
    # This small board cache is already pinned by the PS-only platform workflow.
    spec = json.loads((ROOT/'hardware/platform/board_source.json').read_text())
    cache = BUILD/'arm_platform/officialsource'/('digilent-vivado-boards-'+spec['commit'])
    for entry in spec['files']:
        source = cache/entry['local']
        if sha(source) != entry['sha256']:
            raise ValueError('Pinned board source changed: '+str(source))
        destination = out/'board_source'/entry['local']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return hashes, spec


def launch(out, name, script, arguments, manifest):
    manifest['scheduling_checks'].append(process_guard())
    command = [str(VIVADO), '-mode', 'batch', '-notrace', '-log', name+'.log', '-journal', name+'.jou',
               '-source', str(out/'source'/script), '-tclargs', *map(str, arguments)]
    manifest.setdefault('commands', []).append(command)
    dump(out/'run_manifest.json', manifest)
    with (out/(name+'_console.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(name+' Vivado exit '+str(result.returncode))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--mode', choices=['prepare', 'bd', 'sim', 'all'], default='all')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--verified-simulation', type=Path, help='reuse finalized AXI/unit simulation only when RTL, benches and every vector are identical')
    args = parser.parse_args()
    if not args.run_id.replace('_', '').replace('-', '').isalnum():
        parser.error('Use a new simple run ID')
    guard = process_guard() if args.mode != 'prepare' else dict(checked_at_utc=now(), note='offline snapshot only')
    out = BUILD/'system_fixed'/args.run_id
    if args.mode in ('bd','all') and len(str(out))>40:
        parser.error('Vivado Windows IP checkpoint paths require a short output root (at most 40 characters); use a run ID of at most 8 characters in this workspace')
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(status='preparing', started_at_utc=now(), mode=args.mode, smoke=args.smoke,
                    tool='Vivado2024.2', part='xc7z020clg400-1', physical_board_accessed=False,
                    evaluation_audio_used=False, numerical_accuracy='NOT_ACCEPTED',
                    fixed_baseline=str(GOLD), fixed_baseline_sha256=sha(GOLD/'run_manifest.json'),
                    contract=str(CONTRACT), contract_sha256=sha(CONTRACT/'contract.json'),
                    scheduling_checks=[guard], validation=dict(simulation='NOT_RUN', board_implementation='NOT_RUN', board_execution='NOT_RUN'))
    try:
        manifest['cases'] = stage_inputs(out, args.smoke)
        manifest['frames'] = sum(c['frames'] for c in manifest['cases'])
        manifest['source_sha256'], manifest['board_source'] = snapshot(out)
        manifest['status'] = 'prepared'
        dump(out/'run_manifest.json', manifest)
        if args.mode in ('sim', 'all'):
            manifest['status'] = 'running'
            if args.verified_simulation:
                prior=args.verified_simulation.resolve();previous=json.loads((prior/'run_manifest.json').read_text())
                prior_hashes=json.loads((prior/'artifact_hashes.json').read_text())
                if prior_hashes.get('run_manifest.json')!=sha(prior/'run_manifest.json'):
                    raise ValueError('Simulation source manifest integrity failed')
                if previous['status'] not in ('complete','failed') or previous['cases']!=manifest['cases']:
                    raise ValueError('Simulation source is unfinished or has different cases')
                for rel,digest in manifest['source_sha256'].items():
                    if (rel.startswith('hardware/') and rel.endswith(('.sv','.mem'))) or rel in ('verification/system/tb_fixed_accel.sv','verification/system/tb_mfcc_mmio.sv'):
                        if previous['source_sha256'].get(rel)!=digest or sha(prior/'source'/rel)!=digest:
                            raise ValueError('Simulation reuse source differs: '+rel)
                for name in ['pcm.mem','mfcc.mem','shift.mem','ps_pcm.mem','ps_mfcc.mem','ps_shift.mem','cases.txt']:
                    if sha(prior/name)!=sha(out/name):raise ValueError('Simulation reuse vector differs: '+name)
                for name in ['simulation.json','unit_simulation.json','tb_fixed_accel_simulate.log','tb_mfcc_mmio_simulate.log']:
                    if prior_hashes.get(name)!=sha(prior/name):
                        raise ValueError('Simulation evidence integrity failed: '+name)
                    shutil.copy2(prior/name,out/name)
                manifest['simulation_source']=dict(path=str(prior),manifest_sha256=sha(prior/'run_manifest.json'),
                    artifacts_sha256=sha(prior/'artifact_hashes.json'),same_RTL_TB_and_vectors=True)
            else:
                launch(out, 'simulation', 'hardware/system/simulate_fixed_system.tcl', [out], manifest)
            manifest['simulation'] = json.loads((out/'simulation.json').read_text())
            manifest['unit_simulation'] = json.loads((out/'unit_simulation.json').read_text())
            if manifest['simulation']['status'] != 'PASS' or manifest['simulation']['frames'] != manifest['frames'] or manifest['unit_simulation']['status'] != 'PASS':
                raise ValueError('Incomplete integration simulation')
            manifest['validation']['simulation'] = 'PASS'
        if args.mode == 'bd':
            launch(out, 'block_design', 'hardware/system/create_fixed_system.tcl', [out, out/'board_source/board_files', 'prepare'], manifest)
            manifest['validation']['block_design_generation'] = 'PASS'
        if args.mode == 'all':
            launch(out, 'implementation', 'hardware/system/create_fixed_system.tcl', [out, out/'board_source/board_files', 'bitstream'], manifest)
            timing = json.loads((out/'reports/timing.json').read_text())
            if min(timing.values()) < 0:
                raise ValueError('Full system timing failed')
            bit = out/'design/zybo_z7_20_fixed.bit'
            xsa = out/'design/zybo_z7_20_fixed.xsa'
            if not bit.is_file() or not xsa.is_file():
                raise ValueError('Missing system bitstream/XSA')
            with zipfile.ZipFile(xsa) as archive:
                members = archive.namelist()
                bits = [n for n in members if n.endswith('.bit')]
                if len(bits) != 1 or hashlib.sha256(archive.read(bits[0])).hexdigest() != sha(bit):
                    raise ValueError('XSA embedded bitstream mismatch')
                if not any(n.endswith('ps7_init.tcl') for n in members):
                    raise ValueError('XSA has no PS initialization')
            manifest['timing'] = timing
            manifest['outputs'] = dict(bitstream=dict(path=str(bit), sha256=sha(bit)), xsa=dict(path=str(xsa), sha256=sha(xsa), members=members))
            manifest['validation']['board_implementation'] = 'PASS'
            launch(out, 'ps_simulation', 'hardware/system/simulate_ps_system.tcl', [out], manifest)
            manifest['ps_simulation'] = json.loads((out/'ps_simulation.json').read_text())
            if manifest['ps_simulation']['status'] != 'PASS':
                raise ValueError('PS bus-model integration simulation failed')
            manifest['validation']['PS_bus_model_simulation'] = 'PASS'
        manifest['status'] = 'prepared' if args.mode in ('prepare', 'bd') else 'complete'
    except Exception as error:
        manifest['status'] = 'failed'
        manifest['error'] = str(error)
        raise
    finally:
        manifest['finished_at_utc'] = now()
        dump(out/'run_manifest.json', manifest)
        dump(out/'artifact_hashes.json', {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob('*'))
                                        if p.is_file() and p.name != 'artifact_hashes.json' and p.suffix not in ('.lock', '.lck')})
    print(json.dumps(dict(run=str(out), status=manifest['status'], validation=manifest['validation'], timing=manifest.get('timing')), indent=2))


if __name__ == '__main__':
    sys.exit(main())
