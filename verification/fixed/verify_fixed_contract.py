"""Independently verify a published FFT/Power/Mel bundle; never alter it."""
from __future__ import annotations
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True  # Verification must not add caches to a published bundle.
import numpy as np


def main():
    ap=argparse.ArgumentParser();ap.add_argument('bundle',type=Path);ap.add_argument('--prepublish',action='store_true')
    args=ap.parse_args();base=args.bundle.resolve();checks=0
    def require(condition,label):
        nonlocal checks
        checks+=1
        if not condition:raise ValueError(label)
    def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    hashes=json.loads((base/'artifact_hashes.json').read_text(encoding='utf-8'))
    for rel,want in hashes.items():
        path=(base/rel).resolve();require(path.is_relative_to(base),'unsafe package path')
        require(path.is_file() and digest(path)==want,'hash mismatch '+rel)
    if not args.prepublish:
        pub=json.loads((base/'PUBLISHED.json').read_text(encoding='utf-8'))
        require(pub['status']=='PUBLISHED','publication marker')
        require(pub['contract_sha256']==digest(base/'contract.json'),'contract identity')
        require(pub['artifact_manifest_sha256']==digest(base/'artifact_hashes.json'),'artifact identity')
        require(pub['verification_sha256']==digest(base/'verification.json'),'verification identity')
    m=json.loads((base/'contract.json').read_text(encoding='utf-8'))
    require(m['normal_frames']==616,'all616 normal frames')
    require(len(m['cases'])==24,'24cases')
    require(sum(c['frames'] for c in m['cases'] if c['group']=='development')==534,'full dev')
    arrays={}
    for name,info in m['vector_files'].items():
        path=base/info['file'];require(digest(path)==info['sha256'],'array hash '+name)
        arrays[name]=np.fromfile(path,dtype=info['dtype']).reshape(info['shape'])
    require(arrays['fft_real'].shape==(m['total_frames'],512),'complete complex real512')
    require(arrays['fft_imag'].shape==(m['total_frames'],512),'complete complex imag512')
    weights=json.loads((base/'coefficients/mel_u17_f16.json').read_text(encoding='utf-8'))
    sys.path.insert(0,str(base/'model_snapshot'))
    from software.fixed_model.contract import spectral_frame
    from software.fixed_model.fft_bitmodel import load_twiddle_rom
    twiddle=load_twiddle_rom(str(base/'coefficients/twiddle_1024_w16.mem'))
    overflow=0
    for frame in range(m['total_frames']):
        meta=[int(v) for v in arrays['meta'][frame]];fid,s,ov,p_exp,m_exp=meta
        require(fid==frame and p_exp==-27-2*s and m_exp==-43-2*s,'frame metadata/scale')
        answer=spectral_frame(arrays['input_real'][frame].tolist(),arrays['input_imag'][frame].tolist(),s,twiddle,weights)
        for name in ('fft_real','fft_imag','power','mel'):
            require(answer[name]==arrays[name][frame].tolist(),'bit mismatch '+name)
        require(answer['fft_overflow']==bool(ov),'overflow mismatch')
        re=arrays['fft_real'][frame].tolist();im=arrays['fft_imag'][frame].tolist()
        power=[int(r)**2+int(j)**2 for r,j in zip(re[:257],im[:257])]
        mel=[sum(power[k]*int(w) for k,w in enumerate(row)) for row in weights]
        require(power==arrays['power'][frame].tolist(),'independent Power')
        require(mel==arrays['mel'][frame].tolist(),'independent Mel')
        require(all(0<=v<1<<40 for v in power),'Power40 bound')
        require(all(0<=v<1<<60 for v in mel),'Mel60 bound')
        if frame<616:require(not ov,'normal input overflow')
        overflow+=ov
    require(overflow>0,'includes intentional overflow')
    b=json.loads((base/'vectors/boundaries.json').read_text(encoding='utf-8'))
    for v in b['rounding']:
        exact=Fraction(v['value'],1<<v['shift'])
        require(round(exact)==v['expected'],'negative/positive ties-even')
    floor=Fraction(b['floor_rational']['numerator'],b['floor_rational']['denominator'])
    for v in b['floor']:
        require((v['T']*Fraction(2)**v['mel_exp2']<floor)==v['below_floor'],'exact floor edge')
    for v in b['bfp']:
        peak=Fraction(v['peak_num'],v['peak_den'])
        s=-2
        for candidate in range(-2,25):
            if peak*Fraction(2)**(candidate-1)*32768<=31949:s=candidate
        require(s==v['expected_s'],'BFP edge')
    for v in b['signed20_boundaries']:
        x=v['value'];wrapped=x&((1<<20)-1)
        if wrapped&(1<<19):wrapped-=1<<20
        require(wrapped==v['wrapped'] and (x!=wrapped)==v['overflow'],'signed wrapping')
    print(json.dumps(dict(status='PASS',checks=checks,frames=m['total_frames'],
        complex_fft_bins=m['total_frames']*512,overflow_stress_frames=overflow,
        normal_frames=616,all_integer_vectors_recomputed=True,hashes=len(hashes)),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
