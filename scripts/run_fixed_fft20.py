"""Immutable Vivado 2024.2 FFT20 bit-transaction and OOC implementation run.

Original FFT and historical numerical study are never modified. A source
snapshot, exact packed integer vectors, all512 outputs and hashes are retained.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'software'))
from fixed_model.fft_bitmodel import load_twiddle_rom
from fixed_model.fft_bitmodel_wide import FftWidthConfig,fft_fixed_natural_wide

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,obj):Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def directed_frames():
    rng=random.Random(2026100401)
    z=[0]*512
    result=[('silence',z,z)]
    for value in [1,-1,255,-255,524287,-524288,524272,-524272]:
        r=z.copy();r[0]=value
        result.append((f'impulse_{value}',r,z))
    for value in [1,-1,3,-3,524287,-524288,511184,-511184]:
        result.append((f'dc_{value}',[value]*512,z))
    result.append(('fullscale_alternating',[-524288 if i%2 else 524287 for i in range(512)],z))
    for k in [1,3,5,17,32,127]:
        # Exact sign patterns include boundary and intermediate twiddle stress.
        r=[-524288 if ((i*k)%512)<256 else 524287 for i in range(512)]
        result.append((f'real_square_{k}',r,z))
    for n in range(8):
        result.append((f'complex_extreme_{n}',[rng.choice([-524288,524287]) for _ in z],[rng.choice([-524288,524287]) for _ in z]))
    result.append(('recovery_after_overflow',[65536]*512,z))
    for n in range(8):
        result.append((f'q16_promoted_{n}',[rng.randrange(-31949,31950)<<4 for _ in z],[0]*512))
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--run-id',required=True)
    ap.add_argument('--vectors-json',type=Path,help='Additional frames [{name,re:[512],im:[512]}] at raw signed20 input boundary')
    ap.add_argument('--implement',action='store_true')
    ap.add_argument('--vivado-bin',type=Path,default=Path('C:/Xilinx/Vivado/2024.2/bin'))
    args=ap.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]+',args.run_id):ap.error('unsafe run id')
    out=PROJECT.parent/'build/fixed_fft'/args.run_id
    if out.exists():ap.error('cannot overwrite a run')
    provenance=json.loads((PROJECT/'hardware/fixed/fft/PROVENANCE.json').read_text())
    for entry in provenance['files']:
        if sha(entry['source'])!=entry['source_sha256']:
            raise ValueError('preserved original hash mismatch: '+entry['source'])
        if sha(PROJECT/'hardware/fixed/fft'/entry['copy'])!=entry['copy_sha256']:
            raise ValueError('work copy hash mismatch: '+entry['copy'])
    out.mkdir(parents=True)
    sources=sorted((PROJECT/'hardware/fixed/fft').glob('*'))
    sources+=sorted((PROJECT/'verification/fixed/fft20').glob('*'))
    sources+=[Path(__file__).resolve(),PROJECT/'software/fixed_model/fft_bitmodel.py',PROJECT/'software/fixed_model/fft_bitmodel_wide.py',PROJECT/'software/fixed_model/qnum.py']
    snap=out/'source'
    for p in sources:
        rel=p.relative_to(PROJECT);dst=snap/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
    manifest=dict(status='running',tool='Vivado2024.2',part='xc7z020clg400-1',started_at_utc=datetime.now(timezone.utc).isoformat(),
                  original_preserved=True,board_executed=False,numerical_accuracy_pass=False,
                  source_sha256={str(p.relative_to(PROJECT)).replace('\\','/'):sha(snap/p.relative_to(PROJECT)) for p in sources})
    dump(out/'manifest.json',manifest)
    try:
        frames=directed_frames()
        if args.vectors_json:
            extra=json.loads(args.vectors_json.read_text(encoding='utf-8-sig'))
            if isinstance(extra,dict):extra=extra['frames']
            frames.extend((d['name'],d['re'],d.get('im',[0]*512)) for d in extra)
            manifest['external_vectors']=dict(path=str(args.vectors_json),sha256=sha(args.vectors_json),frames=len(extra))
        if len(frames)>1024:raise ValueError('TB supports at most1024frames')
        twr,twi=load_twiddle_rom(snap/'hardware/fixed/fft/twiddle_1024_w16.mem')
        cfg=FftWidthConfig(data_width=20,data_frac=19,output_width=20,output_frac=19,label='rtl20')
        inputs=[];expected=[];ov=[]
        for name,r,i in frames:
            if len(r)!=512 or len(i)!=512:raise ValueError(name+' length')
            er,ei,overflow=fft_fixed_natural_wide(r,i,twr,twi,cfg)
            inputs.extend(zip(r,i));expected.extend(zip(er,ei));ov.append(int(overflow))
        for name,data in [('inputs',inputs),('expected',expected)]:
            (out/f'{name}.mem').write_text(''.join(f'{((r&0xfffff)<<20)|(i&0xfffff):010x}\n' for r,i in data),encoding='ascii')
        (out/'overflow.mem').write_text(''.join(f'{v}\n' for v in ov),encoding='ascii')
        (out/'frame_count.txt').write_text(str(len(frames))+'\n',encoding='ascii')
        dump(out/'vectors.json',dict(frames=[dict(name=f[0],expected_overflow=ov[n]) for n,f in enumerate(frames)],format='40-bit hex {signed20re,signed20im}',bins_per_frame=512,rounding='ties-to-even',normalization_shift=9))
        shutil.copy2(snap/'hardware/fixed/fft/twiddle_1024_w16.mem',out/'twiddle_1024_w16.mem')
        (out/'run_sim.tcl').write_text('run all\nquit\n',encoding='ascii')
        def run(tool,argv,logname):
            command=[str(args.vivado_bin/(tool+'.bat')),*map(str,argv)]
            manifest.setdefault('commands',[]).append(command)
            with (out/logname).open('w',encoding='utf-8') as log:
                proc=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
            if proc.returncode:raise RuntimeError(f'{tool} failed ({proc.returncode}); {logname}')
        run('xvlog',['--sv',*sorted((snap/'hardware/fixed/fft').glob('*.sv')),snap/'verification/fixed/fft20/tb_fft20.sv'],'compile.log')
        run('xelab',['tb_fft20','-s','fft20_sim','--debug','typical'],'elaborate.log')
        run('xsim',['fft20_sim','-tclbatch','run_sim.tcl'],'simulate.log')
        if 'FFT20_PASS' not in (out/'simulate.log').read_text():raise RuntimeError('missing TB success marker')
        rows=list(csv.DictReader((out/'fft20_outputs.csv').open()))
        if len(rows)!=len(expected):raise ValueError('output count mismatch')
        for n,row in enumerate(rows):
            if (int(row['frame']),int(row['bin']),int(row['re']),int(row['im']),int(row['overflow']))!=(n//512,n%512,*expected[n],ov[n//512]):
                raise ValueError(f'independent CSV mismatch {n}')
        manifest['simulation']=dict(status='passed',frames=len(frames),complex_bins=len(rows),integer_components_compared=2*len(rows),mismatches=0,overflow_frames=sum(ov),partial_frame_reset_samples=173,frame_internal_gaps=True,continuous_frames=True,final_frame_drained=True,output_stall_supported=False)
        dump(out/'manifest.json',manifest)
        if args.implement:
            run('vivado',['-mode','batch','-notrace','-source',snap/'verification/fixed/fft20/fft20_impl.tcl','-log',out/'vivado.log','-journal',out/'vivado.jou','-tclargs',snap,out],'implementation_console.log')
            summary=(out/'implementation_status.txt').read_text()
            values=dict(line.split('=',1) for line in summary.splitlines() if '=' in line)
            setup=float(values['setup_slack_ns']);hold=float(values['hold_slack_ns'])
            manifest['implementation']=dict(status='post_route_complete',summary=summary,
                                            setup_slack_ns=setup,hold_slack_ns=hold,timing_pass=setup>=0 and hold>=0)
        else:manifest['implementation']=dict(status='not_run')
        manifest['status']='complete'
    except Exception as exc:
        manifest['status']='failed';manifest['error']=str(exc)
        raise
    finally:
        manifest['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        manifest['artifact_sha256']={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='manifest.json'}
        dump(out/'manifest.json',manifest)
    print(json.dumps(dict(run=str(out),simulation=manifest['simulation'],implementation=manifest['implementation']),indent=2))

if __name__=='__main__':main()
