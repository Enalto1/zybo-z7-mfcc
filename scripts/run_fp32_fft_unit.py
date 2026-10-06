"""Simulate actual generated AMD FFT IP against exact input words and independent DFT expectations."""
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
import sys
import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
ROOT=PROJECT.parent/'build/fp32_hw'
NAMES=['impulse_n0','dc_one','cos_bin32','sin_bin32','impulse_n17']
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x):Path(p).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def hexfile(path,array,dtype,width):
    ints=np.ascontiguousarray(array,dtype=dtype).view('<u4' if width==8 else '<u8').ravel()
    path.write_text(''.join(f'{int(x):0{width}x}\n' for x in ints),encoding='ascii')

def vectors(out):
    n=np.arange(512,dtype=np.float64)
    data=np.zeros((5,512),dtype=np.float32)
    data[0,0]=1;data[1]=1;data[2]=np.cos(2*np.pi*32*n/512);data[3]=np.sin(2*np.pi*32*n/512);data[4,17]=1
    # Explicit scalar DFT sums are independent of the IP and any NumPy FFT API.
    expected=np.empty((5,512),dtype=np.complex128)
    for case in range(5):
        for k in range(512):
            value=0j
            for sample in range(512):
                angle=-2*np.pi*k*sample/512
                value+=float(data[case,sample])*complex(np.cos(angle),np.sin(angle))
            expected[case,k]=value
    hexfile(out/'inputs.mem',data,'<f4',8)
    hexfile(out/'expected_real.mem',expected.real,'<f8',16)
    hexfile(out/'expected_imag.mem',expected.imag,'<f8',16)
    dump(out/'vectors.json',dict(cases=NAMES,transform='negative exponent, no normalization, natural output order',
        input='exact binary32 words; imaginary part +0',oracle='explicit float64 scalar complex DFT of those exact input words',
        atol=2e-5,rtol=2e-5,criterion='complex error magnitude <= atol+rtol*complex reference magnitude',
        config_word='0x000001',config_intent='FWD=1; all scale schedule bits0; gain measured by unit cases',
        files={p.name:sha(p) for p in (out/'inputs.mem',out/'expected_real.mem',out/'expected_imag.mem')}))
    return expected

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--ip-root',type=Path,default=ROOT/'ip_04')
    parser.add_argument('--vivado',default='C:/Xilinx/Vivado/2024.2/bin/vivado.bat')
    args=parser.parse_args()
    if not re.fullmatch(r'fft_unit_[A-Za-z0-9_-]+',args.run_id):parser.error('Use a new fft_unit_ run name')
    out=ROOT/args.run_id
    if out.exists():parser.error('Cannot overwrite evidence')
    out.mkdir(parents=True)
    files=['scripts/run_fp32_fft_unit.py','scripts/fp32_fft_unit.tcl','verification/fp32/f1_fft/tb_fp32_fft_unit.sv']
    manifest=dict(status='running',started_at_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256={p:sha(PROJECT/p) for p in files},ip_root=str(args.ip_root),board_executed=False,
        full_mfcc_validated=False,tool_target='Vivado2024.2',part='xc7z020clg400-1')
    try:
        ipmanifest=json.loads((args.ip_root/'ip_manifest.json').read_text(encoding='utf-8-sig'))
        if ipmanifest.get('status')!='generated_not_synthesized' or ipmanifest.get('aclken_enabled') is not True:
            raise ValueError('This CE test requires a completed generated IP set with aclken enabled')
        manifest['ip_manifest_sha256']=sha(args.ip_root/'ip_manifest.json')
        for rel,digest in ipmanifest['output_hashes'].items():
            if 'fp32_fft512' in rel and sha(args.ip_root/rel)!=digest:raise ValueError('Pinned FFT artifact mismatch: '+rel)
        xci=args.ip_root/'project/fp32_ips.srcs/sources_1/ip/fp32_fft512/fp32_fft512.xci'
        before=sha(xci);manifest['source_xci_sha256']=before
        parameters=json.loads(xci.read_text(encoding='utf-8-sig'))['ip_inst']['parameters']['component_parameters']
        if parameters['aclken'][0]['value']!='true':raise ValueError('FFT aclken port is not enabled')
        source=out/'source'
        for rel in files:
            target=source/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/rel,target)
            if sha(target)!=manifest['source_sha256'][rel]:raise ValueError('Source changed during snapshot: '+rel)
        expected=vectors(out)
        command=[args.vivado,'-mode','batch','-source',str(source/'scripts/fp32_fft_unit.tcl'),'-notrace',
            '-log',str(out/'vivado.log'),'-journal',str(out/'vivado.jou'),'-tclargs',str(source),str(out),str(xci)]
        manifest['command_argv']=command
        with (out/'console.log').open('w',encoding='utf-8')as log:
            result=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
        if sha(xci)!=before:raise ValueError('Pinned source XCI changed during unit run')
        if result.returncode:raise RuntimeError('Vivado failed; see console.log')
        resultfiles=list(out.rglob('fft_results.csv'))
        if len(resultfiles)!=1:raise ValueError('Expected one captured FFT result file')
        rows=list(csv.DictReader(resultfiles[0].open()))
        if len(rows)!=2560:raise ValueError('FFT output count differs from 5x512')
        actual=np.empty((5,512),dtype=np.complex128)
        for i,row in enumerate(rows):
            case,k=divmod(i,512)
            if (int(row['case']),int(row['bin']))!=(case,k):raise ValueError('CSV frame/bin order error')
            words=np.array([int(row['real_hex'],16),int(row['imag_hex'],16)],dtype='<u4').view('<f4')
            actual[case,k]=complex(float(words[0]),float(words[1]))
        if not np.isfinite(actual).all():raise ValueError('Nonfinite output')
        errors=np.abs(actual-expected);limits=2e-5+2e-5*np.abs(expected)
        cases={name:dict(max_abs=float(errors[i].max()),rmse=float(np.sqrt(np.mean(errors[i]**2))),
            violations=int(np.count_nonzero(errors[i]>limits[i])),outputs=512) for i,name in enumerate(NAMES)}
        analytics=dict(impulse_unity_max_error=float(np.abs(actual[0]-1).max()),dc_bin0=[actual[1,0].real,actual[1,0].imag],
            cosine_bin32=[actual[2,32].real,actual[2,32].imag],cosine_bin480=[actual[2,480].real,actual[2,480].imag],
            sine_bin32=[actual[3,32].real,actual[3,32].imag],sine_bin480=[actual[3,480].real,actual[3,480].imag])
        dump(out/'comparison.json',dict(cases=cases,analytic_observations=analytics,passed=bool(np.all(errors<=limits))))
        simlogs=list(out.rglob('simulate.log'))
        if len(simlogs)!=1 or 'FFT_UNIT_PASS' not in simlogs[0].read_text(errors='replace'):raise ValueError('FFT simulation protocol/numerical tests did not pass')
        simtext=simlogs[0].read_text(errors='replace')
        coverage=re.search(r'FFT_CE_METRICS cycles=(\d+) config=(\d+) input=(\d+) compute=(\d+) output=(\d+) post_accept=(\d+) config_handshakes=(\d+) input_handshakes=(\d+) output_handshakes=(\d+)',simtext)
        if not coverage:raise ValueError('Missing actual clock-enable coverage')
        ce=dict(zip(('cycles','config','input','compute','output','post_accept','config_handshakes','input_handshakes','output_handshakes'),map(int,coverage.groups())))
        if tuple(ce.values())!=(739,19,155,265,115,185,1,2560,2560):raise ValueError('Clock-enable coverage differs from frozen test')
        if not np.all(errors<=limits):raise ValueError('Independent host-side FFT comparison failed')
        manifest.update(status='passed_fft_unit_only',cases=cases,analytic_observations=analytics,clock_enable_coverage=ce)
        print(json.dumps({'status':manifest['status'],'cases':cases,'analytic_observations':analytics},indent=2));return 0
    except Exception as error:
        manifest.update(status='failed',error=str(error));print(str(error),file=sys.stderr);return 1
    finally:
        manifest['completed_at_utc']=datetime.now(timezone.utc).isoformat();dump(out/'run_manifest.json',manifest)
        dump(out/'artifact_manifest.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*'))
            if p.is_file() and p.name!='artifact_manifest.json' and '.Xil' not in p.parts})

if __name__=='__main__':raise SystemExit(main())
