"""Verify the actual signed16 -> FFT20 -> Power40 -> Mel60 RTL chain.

Uses a published, hash-checked C contract bundle as the external oracle. Saves
an immutable source snapshot and all512 complex FFT/26 Mel output transactions.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,o):Path(p).write_text(json.dumps(o,indent=2)+'\n',encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True)
    p.add_argument('--contract',type=Path,required=True)
    p.add_argument('--limit',type=int)
    p.add_argument('--implement',action='store_true')
    a=p.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]+',a.run_id):p.error('simple fresh run id required')
    out=PROJECT.parent/'build/fixed_spectral'/a.run_id
    if out.exists():p.error('cannot overwrite an immutable run')
    publication=json.loads((a.contract/'PUBLISHED.json').read_text())
    hashes=json.loads((a.contract/'artifact_hashes.json').read_text())
    if publication['status']!='PUBLISHED' or sha(a.contract/'contract.json')!=publication['contract_sha256']:
        raise ValueError('contract publication marker mismatch')
    if sha(a.contract/'artifact_hashes.json')!=publication['artifact_manifest_sha256']:
        raise ValueError('artifact manifest mismatch')
    for rel,digest in hashes.items():
        if sha(a.contract/rel)!=digest:raise ValueError('contract artifact hash mismatch: '+rel)
    c=json.loads((a.contract/'contract.json').read_text())
    if c['normal_frames']!=616:raise ValueError('expected full baseline corpus')
    arrays={}
    for key,m in c['vector_files'].items():
        arrays[key]=np.fromfile(a.contract/m['file'],dtype=m['dtype']).reshape(m['shape'])
    frames=c['total_frames'] if a.limit is None else min(a.limit,c['total_frames'])
    if not 1<=frames<=1024:raise ValueError('TB frame capacity')
    out.mkdir(parents=True)
    sources=sorted((PROJECT/'hardware/fixed/fft').glob('*'))
    sources+=sorted((PROJECT/'hardware/fixed/power_mel').glob('*'))
    sources+=[PROJECT/'hardware/fixed/fixed_spectral_top.sv',Path(__file__).resolve()]
    sources+=sorted((PROJECT/'verification/fixed/spectral').glob('*'))
    snap=out/'source'
    for source in sources:
        dest=snap/source.relative_to(PROJECT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    manifest=dict(status='running',contract=str(a.contract.resolve()),contract_sha256=sha(a.contract/'contract.json'),
                  frames=frames,normal_frames_available=616,normal_frames_executed=min(frames,616),
                  evaluation_audio_used=False,board_run=False,numerical_accuracy_pass=False,
                  tool='Vivado2024.2',part='xc7z020clg400-1',started_at_utc=datetime.now(timezone.utc).isoformat(),
                  source_sha256={x.relative_to(PROJECT).as_posix():sha(snap/x.relative_to(PROJECT)) for x in sources})
    dump(out/'manifest.json',manifest)
    try:
        def words(name,values,width):
            (out/name).write_text(''.join(f'{int(v):0{width}x}\n' for v in values),encoding='ascii')
        r=arrays['input_real'][:frames].ravel();i=arrays['input_imag'][:frames].ravel()
        words('inputs.mem',((int(x)&65535)<<16|(int(y)&65535) for x,y in zip(r,i)),8)
        r=arrays['fft_real'][:frames].ravel();i=arrays['fft_imag'][:frames].ravel()
        words('fft_expected.mem',((int(x)&0xfffff)<<20|(int(y)&0xfffff) for x,y in zip(r,i)),10)
        words('power_expected.mem',arrays['power'][:frames].ravel(),10)
        words('mel_expected.mem',arrays['mel'][:frames].ravel(),15)
        words('bfp.mem',(int(v)&255 for v in arrays['meta'][:frames,1]),2)
        words('overflow.mem',arrays['meta'][:frames,2],1)
        (out/'frame_count.txt').write_text(str(frames)+'\n',encoding='ascii')
        shutil.copy2(snap/'hardware/fixed/fft/twiddle_1024_w16.mem',out/'twiddle_1024_w16.mem')
        coeff=np.fromfile(a.contract/c['mel']['coefficients']['file'],dtype='<u4')
        words('mel_fw16.mem',list(coeff)+[0]*(8192-len(coeff)),5)
        if (out/'mel_fw16.mem').read_bytes()!=(snap/'hardware/fixed/power_mel/mel_fw16.mem').read_bytes():
            raise ValueError('RTL Mel table differs from published contract')
        (out/'run_sim.tcl').write_text('run all\nquit\n')
        def run(tool,args,logfile):
            command=[f'C:/Xilinx/Vivado/2024.2/bin/{tool}.bat',*map(str,args)]
            manifest.setdefault('commands',[]).append(command)
            with (out/logfile).open('w',encoding='utf-8') as log:
                proc=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
            if proc.returncode:raise RuntimeError(f'{tool} failed: {logfile}')
        rtl=sorted((snap/'hardware/fixed/fft').glob('*.sv'))+[snap/'hardware/fixed/power_mel/fixed_spectral_tail.sv',snap/'hardware/fixed/fixed_spectral_top.sv']
        run('xvlog',['--sv',*rtl,snap/'verification/fixed/spectral/tb_fixed_spectral.sv'],'compile.log')
        run('xelab',['tb_fixed_spectral','-L','xpm','-L','unisims_ver','-s','spectral_sim','--debug','typical'],'elaborate.log')
        run('xsim',['spectral_sim','-tclbatch','run_sim.tcl'],'simulate.log')
        log=(out/'simulate.log').read_text()
        if 'SPECTRAL_PASS' not in log:raise RuntimeError('TB did not complete')
        fftrows=list(csv.DictReader((out/'fft_outputs.csv').open()))
        melrows=list(csv.DictReader((out/'mel_outputs.csv').open()))
        if len(fftrows)!=512*frames or len(melrows)!=26*frames:raise ValueError('CSV output count')
        for ordinal,row in enumerate(fftrows):
            f,k=divmod(ordinal,512)
            if tuple(int(row[x]) for x in ('frame','bin','re','im'))!=(f,k,int(arrays['fft_real'][f,k]),int(arrays['fft_imag'][f,k])):
                raise ValueError('FFT CSV mismatch')
        for ordinal,row in enumerate(melrows):
            f,m=divmod(ordinal,26)
            if tuple(int(row[x]) for x in ('frame','band','mel','frame_id','bfp_s','overflow'))!=(f,m,int(arrays['mel'][f,m]),1000+7*f,int(arrays['meta'][f,1]),int(arrays['meta'][f,2])):
                raise ValueError('Mel CSV mismatch')
        marker=next(line for line in log.splitlines() if 'SPECTRAL_PASS' in line)
        manifest['simulation']=dict(status='passed',fft_complex_bins=len(fftrows),power_bins=257*frames,mel_values=len(melrows),mismatches=0,
                                    reset_partial_input=True,reset_fft_capture=True,reset_stalled_mel=True,
                                    bad_input_last_detected_and_reset_recovered=True,output_stall_stable=True,
                                    continuous_input_requests=True,input_gaps=True,frame_id_remap='1000+7*contract_frame',
                                    overflow_frames=int(arrays['meta'][:frames,2].sum()),marker=marker)
        dump(out/'manifest.json',manifest)
        if a.implement:
            run('vivado',['-mode','batch','-notrace','-source',snap/'verification/fixed/spectral/spectral_impl.tcl','-log',out/'vivado.log','-journal',out/'vivado.jou','-tclargs',snap,out],'implementation_console.log')
            bram_cells=(out/'bram_cells.txt').read_text().splitlines()
            if not any('u_reorder' in x for x in bram_cells):raise ValueError('FFT reorder did not map to actual BRAM')
            if not any('U_POWER_RAM' in x for x in bram_cells):raise ValueError('Power frame bank did not map to actual BRAM')
            summary=(out/'implementation_status.txt').read_text()
            values=dict(line.split('=',1) for line in summary.splitlines() if '=' in line)
            setup=float(values['setup_slack_ns']);hold=float(values['hold_slack_ns'])
            manifest['implementation']=dict(status='post_route_complete',summary=summary,actual_bram_cells=bram_cells,
                                            setup_slack_ns=setup,hold_slack_ns=hold,timing_pass=setup>=0 and hold>=0)
        else:manifest['implementation']=dict(status='not_run')
        manifest['status']='complete'
    except Exception as exc:
        manifest['status']='failed';manifest['error']=str(exc);raise
    finally:
        manifest['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        manifest['artifact_sha256']={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='manifest.json'}
        dump(out/'manifest.json',manifest)
    print(json.dumps(dict(run=str(out),simulation=manifest['simulation'],implementation=manifest['implementation']),indent=2))

if __name__=='__main__':main()
