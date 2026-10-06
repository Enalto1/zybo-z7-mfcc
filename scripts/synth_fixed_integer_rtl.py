"""Synthesize frozen frontend/backend snapshots; timing acceptance is separate from simulation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-run', type=Path, required=True)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--units', default='front,back')
    args = ap.parse_args()
    units = args.units.split(',')
    if not units or len(set(units)) != len(units) or any(u not in ('front', 'back') for u in units):
        ap.error('--units must be a unique subset of front,back')
    out = Path(r'D:\2610_MFCC\build\fixed_integer_rtl') / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.source_run / 'source', out / 'source')
    sources = sorted((out / 'source').rglob('*.sv'))
    tops = {'front': 'mfcc_fixed_frontend', 'back': 'mfcc_fixed_log_dct'}
    tcl = [f'create_project fixed_integer_synth {{{(out / "project").as_posix()}}} -part xc7z020clg400-1',
           'set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]']
    tcl += [f'read_verilog -sv {{{p.as_posix()}}}' for p in sources]
    for unit in units:
        top = tops[unit]
        tcl += [f'synth_design -top {top} -part xc7z020clg400-1 -flatten_hierarchy rebuilt',
                'create_clock -period 10.000 [get_ports clk]',
                'set_input_delay 2.000 -clock clk [get_ports -filter {DIRECTION == IN && NAME != clk}]',
                'set_output_delay 2.000 -clock clk [all_outputs]',
                f'report_utilization -file {top}_utilization.rpt',
                f'report_timing_summary -file {top}_timing.rpt',
                f'report_timing -max_paths 10 -file {top}_paths.rpt',
                f'write_checkpoint {top}.dcp', 'close_design']
    tcl += ['exit']
    (out / 'run.tcl').write_text('\n'.join(tcl) + '\n', encoding='utf-8')
    manifest = {'source_run': str(args.source_run), 'units': units, 'device': 'xc7z020clg400-1',
                'vivado': '2024.2', 'clock_period_ns': 10, 'input_output_delay_ns': 2,
                'sources': {str(p.relative_to(out / 'source')): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (out / 'run_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(out, flush=True)
    result = subprocess.run([r'C:\Xilinx\Vivado\2024.2\bin\vivado.bat', '-mode', 'batch', '-source', 'run.tcl',
                             '-log', 'vivado.log', '-journal', 'vivado.jou'], cwd=out)
    reports = [f'{tops[u]}_{kind}.rpt' for u in units for kind in ('utilization', 'timing', 'paths')]
    passed = result.returncode == 0 and all((out / f).exists() for f in reports)
    (out / 'synthesis_result.json').write_text(json.dumps({
        'status': 'COMPLETE' if passed else 'FAILED', 'vivado_returncode': result.returncode,
        'scope': 'synthesis and estimated timing only; no implementation or board execution',
        'timing_accepted': None, 'reports': reports,
    }, indent=2), encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
