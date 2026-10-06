"""Snapshot, simulate and implement the complete integer PCM->MFCC RTL."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,o):Path(p).write_text(json.dumps(o,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id',required=True)
    ap.add_argument('--model',type=Path,default=ROOT.parent/'build/fixed_full_model/integer_02_20261004')
    ap.add_argument('--contract',type=Path,default=ROOT.parent/'build/fixed_contract/v2_pcm16_mfcc40_20261004_r2')
    ap.add_argument('--smoke',action='store_true',help='use3frames dev plus all boundary cases')
    ap.add_argument('--implementation',choices=['none','synth','route'],default='route')
    ap.add_argument('--verified-simulation',type=Path,help='reuse a completed identical RTL/TB/vector simulation; still create a new implementation snapshot')
    args=ap.parse_args()
    if not args.run_id.replace('_','').replace('-','').isalnum():ap.error('new simple run id required')
    out=ROOT.parent/'build/fixed_full_rtl'/args.run_id;out.mkdir(parents=True,exist_ok=False)
    m=json.loads((args.model/'run_manifest.json').read_text(encoding='utf-8'))
    if m['status']!='complete' or m['total_frames']!=616:raise ValueError('complete616 model required')
    selected=[];pcm_all=[];stages={k:[] for k in ['fft_input','fft_re','fft_im','power_u40','mel_u60','log_q24','mfcc_q24','shift_s']}
    for case in m['cases']:
        if args.smoke and case['group']!='development' and not case['id'].startswith('boundary'):continue
        p=args.model/case['integer_file']
        if sha(p)!=case['sha256']:raise ValueError('model vector hash')
        pcm_p=args.model/'arrays'/case['pcm']['file']
        if sha(pcm_p)!=case['pcm']['sha256']:raise ValueError('PCM hash')
        pcm=np.fromfile(pcm_p,dtype='<i2');n=case['frames']
        if args.smoke and case['group']=='development':n=3;pcm=pcm[:832]
        pcm_all.extend(pcm.tolist())
        with np.load(p,allow_pickle=False) as z:
            for key in stages:stages[key].extend(z[key][:n].tolist())
        selected.append(dict(id=case['id'],group=case['group'],samples=len(pcm),frames=n))
    total=sum(c['frames'] for c in selected)
    def mem(name,rows,width):
        data=np.asarray(rows,dtype=object).ravel();mask=(1<<width)-1
        (out/name).write_text(''.join(f'{int(v)&mask:0{(width+3)//4}x}\n' for v in data),encoding='ascii')
    mem('pcm.mem',pcm_all,16);mem('front.mem',stages['fft_input'],16)
    mem('fft.mem',[(int(r)&0xfffff)<<20|(int(i)&0xfffff) for r,i in zip(np.asarray(stages['fft_re']).ravel(),np.asarray(stages['fft_im']).ravel())],40)
    for name,key,width in [('power.mem','power_u40',40),('mel.mem','mel_u60',60),('log.mem','log_q24',30),('mfcc.mem','mfcc_q24',40),('shift.mem','shift_s',8)]:mem(name,stages[key],width)
    (out/'cases.txt').write_text(f'{len(selected)} {total}\n'+''.join(f'{c["samples"]} {c["frames"]}\n' for c in selected),encoding='ascii')
    sources=sorted((ROOT/'hardware/fixed').rglob('*.sv'))+sorted((ROOT/'hardware/fixed').rglob('*.mem'))
    sources += [Path(__file__),ROOT/'scripts/fixed_full_pipeline.tcl',ROOT/'scripts/generate_sparse_mel.py',ROOT/'verification/fixed/full_pipeline/tb_mfcc_fixed_top.sv',ROOT/'hardware/fixed/fft/PROVENANCE.json']
    hashes={}
    for src in sources:
        rel=src.relative_to(ROOT);dst=out/'source'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst);hashes[rel.as_posix()]=sha(dst)
    for src in ['hardware/fixed/power_mel/mel_fw16.mem','hardware/fixed/power_mel/mel_sparse_fw16.mem','hardware/fixed/fft/twiddle_1024_w16.mem']:shutil.copy2(out/'source'/src,out/Path(src).name)
    (out/'clock.xdc').write_text('create_clock -period 10.000 -name clk [get_ports clk]\nset_input_delay 2.000 -clock clk [get_ports -filter {DIRECTION == IN && NAME != clk}]\nset_output_delay 2.000 -clock clk [all_outputs]\n',encoding='ascii')
    contract=args.contract
    pinned=json.loads((contract/'contract.json').read_text(encoding='utf-8'))
    if sha(args.model/'run_manifest.json')!=pinned['source_run']['sha256']:raise ValueError('model differs from pinned C contract; publish a new contract first')
    manifest=dict(status='running',model=str(args.model),model_manifest_sha256=sha(args.model/'run_manifest.json'),c_contract=dict(path=str(contract),sha256=sha(contract/'contract.json'),publication_sha256=sha(contract/'PUBLISHED.json')),source_sha256=hashes,cases=selected,frames=total,full_corpus=not args.smoke,tool='Vivado2024.2',part='xc7z020clg400-1',clock_ns=10,io_delay_ns=2,board_run=False,accuracy_accepted=False,started_at=datetime.now(timezone.utc).isoformat())
    if args.verified_simulation:
        prior=args.verified_simulation
        old=json.loads((prior/'run_manifest.json').read_text())
        protocol=json.loads((prior/'protocol.json').read_text())
        if old.get('status') not in ('complete','failed'):raise ValueError('simulation source must be finalized before reuse')
        if protocol.get('status')!='PASS' or protocol.get('frames')!=total or old['cases']!=selected:
            raise ValueError('reused simulation is not complete for these cases')
        for rel,digest in hashes.items():
            if rel.startswith(('hardware/','verification/')):
                if old['source_sha256'].get(rel)!=digest or sha(prior/'source'/rel)!=digest:
                    raise ValueError('reused simulation source differs: '+rel)
        for p in list(out.glob('*.mem'))+[out/'cases.txt']:
            previous=prior/p.name
            if not previous.exists() and p.name in ('twiddle_1024_w16.mem','mel_fw16.mem'):
                relative='hardware/fixed/fft/twiddle_1024_w16.mem' if p.name.startswith('twiddle_') else 'hardware/fixed/power_mel/mel_fw16.mem'
                previous=prior/'source'/relative
            if sha(p)!=sha(previous):raise ValueError('reused vector differs: '+p.name)
        shutil.copy2(prior/'protocol.json',out/'protocol.json')
        manifest['simulation_source']=dict(path=str(prior),manifest_sha256=sha(prior/'run_manifest.json'),protocol_sha256=sha(prior/'protocol.json'),matched_rtl_tb_and_vectors=True)
    dump(out/'run_manifest.json',manifest)
    command=['C:/Xilinx/Vivado/2024.2/bin/vivado.bat','-mode','batch','-notrace','-source',str(out/'source/scripts/fixed_full_pipeline.tcl'),'-tclargs',str(out),args.implementation,'reuse' if args.verified_simulation else 'simulate']
    manifest['command']=command
    try:
        with (out/'console.log').open('w',encoding='utf-8') as log:proc=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
        manifest['vivado_exit_code']=proc.returncode
        if proc.returncode:raise RuntimeError(f'Vivado exit{proc.returncode}')
        protocol=json.loads((out/'protocol.json').read_text())
        if protocol['status']!='PASS' or protocol['frames']!=total:raise ValueError('simulation not complete')
        manifest['simulation']=protocol;manifest['implementation']=args.implementation
        if args.implementation!='none':
            brams=(out/'bram_cells.txt').read_text().splitlines()
            if not brams:raise ValueError('missing actual BRAM mapping')
            manifest['actual_bram_cells']=brams
        if args.implementation=='route':manifest['timing']=json.loads((out/'timing.json').read_text())
        manifest['validation_status']=dict(simulation='PASS',synthesis='COMPLETE' if args.implementation!='none' else 'NOT_RUN',implementation='COMPLETE' if args.implementation=='route' else 'NOT_RUN',timing='PASS' if args.implementation=='route' and all(v>=0 for v in manifest['timing'].values()) else 'FAIL' if args.implementation=='route' else 'NOT_RUN',numerical_accuracy='NOT_ACCEPTED',board='NOT_RUN')
        manifest['status']='complete'
    except Exception as e:manifest['status']='failed';manifest['error']=str(e);raise
    finally:
        manifest['finished_at']=datetime.now(timezone.utc).isoformat();dump(out/'run_manifest.json',manifest)
        dump(out/'artifact_hashes.json',{p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name!='artifact_hashes.json'})
    print(json.dumps(dict(run=str(out),status=manifest['status'],simulation=manifest['simulation'],timing=manifest.get('timing'),validation_status=manifest['validation_status']),indent=2))
if __name__=='__main__':main()
