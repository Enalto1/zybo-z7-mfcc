"""Snapshot and verify the fixed frontend II1 candidate against frozen vectors."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--frontend', type=Path, default=ROOT/'hardware/fixed/frontend/mfcc_fixed_frontend.sv')
    ap.add_argument('--model', type=Path, default=ROOT.parent/'build/fixed_full_model/integer_02_20261004')
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--prepare-only', action='store_true')
    args = ap.parse_args()
    if not args.run_id.replace('_', '').replace('-', '').isalnum():
        ap.error('new simple run id required')
    out = ROOT.parent/'build/fixed_optimization'/args.run_id
    out.mkdir(parents=True, exist_ok=False)
    model = json.loads((args.model/'run_manifest.json').read_text(encoding='utf-8'))
    if model['status'] != 'complete' or model['total_frames'] != 616:
        raise ValueError('frozen complete616 model required')
    cases, pcms, windows, fronts, shifts = [], [], [], [], []
    for case in model['cases']:
        if args.smoke and case['group'] != 'development' and not case['id'].startswith('boundary'):
            continue
        data_path = args.model/case['integer_file']
        pcm_path = args.model/'arrays'/case['pcm']['file']
        if sha(data_path) != case['sha256'] or sha(pcm_path) != case['pcm']['sha256']:
            raise ValueError('frozen vector hash mismatch')
        pcm = np.fromfile(pcm_path, dtype='<i2')
        count = case['frames']
        if args.smoke and case['group'] == 'development':
            count, pcm = 3, pcm[:832]
        with np.load(data_path, allow_pickle=False) as values:
            windows.extend(values['windowed_q30'][:count].tolist())
            fronts.extend(values['fft_input'][:count].tolist())
            shifts.extend(values['shift_s'][:count].tolist())
        pcms.extend(pcm.tolist())
        cases.append(dict(id=case['id'], samples=len(pcm), frames=count))
    def mem(name, values, width):
        flat = np.asarray(values, dtype=object).ravel()
        mask = (1 << width)-1
        (out/name).write_text(''.join(f'{int(v)&mask:0{(width+3)//4}x}\n' for v in flat), encoding='ascii')
    mem('pcm.mem', pcms, 16)
    mem('abort_pcm.mem', pcms[:512], 16)
    mem('front.mem', fronts, 16)
    mem('window.mem', windows, 32)
    mem('shift.mem', shifts, 8)
    frames = sum(case['frames'] for case in cases)
    (out/'cases.txt').write_text(f'{len(cases)} {frames}\n'+''.join(f'{c["samples"]} {c["frames"]}\n' for c in cases), encoding='ascii')
    source = out/'source'
    source.mkdir()
    files = [args.frontend, ROOT/'hardware/fixed/frontend/mfcc_fixed_window_coefficient.sv',
             ROOT/'verification/fixed/frontend/tb_mfcc_fixed_frontend_pipeline.sv', Path(__file__)]
    for path in files:
        shutil.copy2(path, source/path.name)
    manifest = dict(status='prepared', started_at=datetime.now(timezone.utc).isoformat(),
                    model=str(args.model), model_manifest_sha256=sha(args.model/'run_manifest.json'),
                    frontend_source=str(args.frontend), source_sha256={p.name: sha(source/p.name) for p in files},
                    cases=cases, frames=frames, tool='Vivado2024.2', clock_ns=10,
                    full_corpus=not args.smoke, numerical_accuracy='NOT_ACCEPTED', board_run=False)
    tcl = [f'set run_dir {{{out.as_posix()}}}', 'if {[version -short] ne "2024.2"} {error "Vivado2024.2 required"}',
           'set_param general.maxThreads 2', 'create_project front_pipe [file join $run_dir project] -part xc7z020clg400-1',
           'set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]',
           'add_files [file join $run_dir source mfcc_fixed_frontend.sv]',
           'add_files [file join $run_dir source mfcc_fixed_window_coefficient.sv]',
           'add_files -fileset sim_1 [file join $run_dir source tb_mfcc_fixed_frontend_pipeline.sv]',
           'set_property top mfcc_fixed_frontend [get_filesets sources_1]',
           'set_property top tb_mfcc_fixed_frontend_pipeline [get_filesets sim_1]',
           'set_property xsim.simulate.runtime all [get_filesets sim_1]',
           'set_property xsim.elaborate.debug_level off [get_filesets sim_1]',
           'set_property -dict [list xsim.simulate.xsim.more_options [list -testplusarg "RUN=$run_dir"]] [get_filesets sim_1]',
           'launch_simulation', 'close_sim', 'close_project', 'exit']
    (out/'run.tcl').write_text('\n'.join(tcl)+'\n', encoding='utf-8')
    def save():
        (out/'run_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    save()
    if args.prepare_only:
        print(json.dumps(dict(run=str(out), status=manifest['status'])))
        return 0
    try:
        with (out/'console.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(['C:/Xilinx/Vivado/2024.2/bin/vivado.bat', '-mode', 'batch', '-notrace', '-source', 'run.tcl'],
                                    cwd=out, stdout=log, stderr=subprocess.STDOUT)
        manifest['vivado_exit_code'] = result.returncode
        if result.returncode != 0:
            raise RuntimeError(f'Vivado exit {result.returncode}')
        protocol = json.loads((out/'protocol.json').read_text())
        if protocol['status'] != 'PASS' or protocol['frames'] != frames:
            raise RuntimeError('missing completed protocol checks')
        manifest['simulation'] = protocol
        manifest['status'] = 'complete'
    except Exception as exc:
        manifest['status'] = 'failed'
        manifest['error'] = str(exc)
        raise
    finally:
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
        (out/'artifact_hashes.json').write_text(json.dumps({p.relative_to(out).as_posix(): sha(p) for p in out.rglob('*')
                                                          if p.is_file() and p.name != 'artifact_hashes.json'}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(run=str(out), status=manifest['status'], simulation=protocol), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
