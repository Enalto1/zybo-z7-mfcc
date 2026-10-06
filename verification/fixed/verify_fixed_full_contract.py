"""Verify an immutable full integer C contract, including every supplied PCM case."""
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
    ap=argparse.ArgumentParser();ap.add_argument('bundle',type=Path);ap.add_argument('--prepublish',action='store_true');args=ap.parse_args()
    base=args.bundle.resolve();checks=0
    def require(ok,why):
        nonlocal checks
        checks+=1
        if not ok:raise ValueError(why)
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    hashes=json.loads((base/'artifact_hashes.json').read_text(encoding='utf-8'))
    for rel,want in hashes.items():
        p=(base/rel).resolve();require(p.is_relative_to(base),'path containment');require(sha(p)==want,'hash '+rel)
    if not args.prepublish:
        pub=json.loads((base/'PUBLISHED.json').read_text())
        for name,key in [('contract.json','contract_sha256'),('artifact_hashes.json','artifact_manifest_sha256'),('verification.json','verification_sha256')]:require(sha(base/name)==pub[key],'publication '+name)
        require(pub['status']=='PUBLISHED','marker')
    contract=json.loads((base/'contract.json').read_text());coeff=json.loads((base/'coefficients/coefficients.json').read_text())
    descriptors=0
    def verify_descriptors(value,label):
        """Every local contract descriptor is authoritative, including nested copies."""
        nonlocal descriptors
        if isinstance(value,dict):
            if 'file' in value:
                descriptors+=1
                rel=value['file'];require(isinstance(rel,str),'descriptor file type '+label)
                path=(base/rel).resolve()
                require(not Path(rel).is_absolute() and path.is_relative_to(base),'descriptor containment '+label)
                require(path.is_file(),'descriptor file missing '+label+': '+rel)
                require('sha256' in value and sha(path)==value['sha256'],'descriptor hash '+label)
                require(rel in hashes and hashes[rel]==value['sha256'],'descriptor indexed '+label)
                require('bytes' in value and path.stat().st_size==value['bytes'],'descriptor bytes '+label)
                if 'dtype' in value or 'shape' in value:
                    require('dtype' in value and 'shape' in value,'descriptor array type/shape '+label)
                    count=1
                    for size in value['shape']:
                        require(isinstance(size,int) and size>=0,'descriptor dimension '+label);count*=size
                    require(count*np.dtype(value['dtype']).itemsize==value['bytes'],'descriptor shape bytes '+label)
            for name,child in value.items():verify_descriptors(child,label+'.'+name)
        elif isinstance(value,list):
            for index,child in enumerate(value):verify_descriptors(child,label+f'[{index}]')
    verify_descriptors(contract,'contract')
    require(contract['front']['mel']['coefficients']==contract['coefficients']['mel_q16'],'one Mel descriptor')
    log=contract['log']
    require((log['log2_W'],log['log2_F'],log['log2_signed'])==(37,30,True),'log2 format')
    require((log['ln2_W'],log['ln2_F'],log['ln2_signed'],log['ln2_int'])==(30,30,False,744261118),'ln2 format')
    require((log['product_W'],log['product_F'],log['product_signed'])==(67,60,True),'log product format')
    numerical_identity_files=0
    if 'supersedes' in contract:
        identity=json.loads((base/contract['supersedes']['numerical_identity']['file']).read_text())
        require(identity['status']=='IDENTICAL','numerical identity status')
        require(identity['previous_contract_sha256']==contract['supersedes']['contract_sha256'],'numerical identity predecessor')
        for rel,want in identity['sha256'].items():
            path=(base/rel).resolve();require(path.is_relative_to(base),'identity containment')
            require(path.is_file() and sha(path)==want,'numerical identity '+rel);numerical_identity_files+=1
        require(numerical_identity_files==identity['files'],'numerical identity count')
    sys.path.insert(0,str(base/'model_snapshot'))
    from software.fixed_model.full_integer import run_pcm,log_integer,choose_bfp_integer,quantize_fft_input,preemphasis_pcm,dct_integer
    from software.fixed_model.fft_bitmodel import load_twiddle_rom
    twiddle=load_twiddle_rom(base/'coefficients/twiddle_1024_w16.mem')
    frames=0;components=0
    def array(info):
        p=base/info['file'];require(sha(p)==info['sha256'],'vector identity')
        return np.fromfile(p,dtype=info['dtype']).reshape(info['shape'])
    for c in contract['cases']:
        pcm=array(c['pcm']);result=run_pcm(pcm,coeff,twiddle);frames+=c['frames']
        for name,info in c['stages'].items():
            expected=array(info);actual=np.asarray(result[name],dtype=expected.dtype).reshape(expected.shape)
            require(np.array_equal(actual,expected),c['id']+'/'+name);components+=expected.size
        require(len(result['fft_re'])==c['frames'],'frame count')
        require(all(len(row)==512 for row in result['fft_re']),'full512 FFT')
    require(frames==616 and len(contract['cases'])==24,'all inputs')
    require(sum(c['frames'] for c in contract['cases'] if c['group']=='development')==534,'development534')
    b=json.loads((base/'vectors/boundaries.json').read_text());floor=Fraction(b['floor_rational']['numerator'],b['floor_rational']['denominator'])
    for row in b['log']:
        value,flag=log_integer(row['T'],row['exponent'])
        require((value,flag)==(row['expected'],row['floor']),'log boundary')
        require(flag==(row['T']*Fraction(2)**row['exponent']<floor),'canonical exact floor')
    for row in b['bfp']:
        values=[row['peak_q30'],-row['peak_q30']];s,clamp=choose_bfp_integer(values);q,clips=quantize_fft_input(values,s)
        require((s,clamp,q,clips)==(row['expected_s'],row['bfp_clamped'],row['expected_q'],row['clips']),'integer BFP')
        candidates=[s for s in range(-2,25) if row['peak_q30']*Fraction(2)**(s-16)<=31949]
        require(s==(0 if row['peak_q30']==0 else max(candidates) if candidates else -2),'independent BFP')
    require(preemphasis_pcm(b['preemphasis']['pcm'])==b['preemphasis']['expected'],'preemph signed extremes')
    for row in b['dct']:require(dct_integer(row['log_q24'],coeff['dct_q30'])==row['expected'],'DCT bounds')
    try:log_integer(-1,-43)
    except (ValueError,OverflowError):require(True,'negative error')
    else:require(False,'negative must error')
    print(json.dumps(dict(status='PASS',checks=checks,cases=24,frames=frames,integer_components_recomputed=components,full_fft_complex_bins=frames*512,hashes=len(hashes),file_descriptors=descriptors,numerically_identical_files=numerical_identity_files,log_boundaries=len(b['log']),bfp_boundaries=len(b['bfp'])),indent=2))
if __name__=='__main__':main()
