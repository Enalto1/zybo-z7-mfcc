"""Publish the frozen PCM16->MFCC integer model as an immutable checked C contract."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--version',required=True)
    ap.add_argument('--model',type=Path,default=ROOT.parent/'build/fixed_full_model/integer_02_20261004')
    ap.add_argument('--spectral-contract',type=Path,default=ROOT.parent/'build/fixed_contract/v1_fft20_power40_mel60_20261004_r2')
    ap.add_argument('--previous-full-contract',type=Path,help='Require identical coefficients, vectors, numerical evidence and integer model to this published version')
    args=ap.parse_args()
    if not args.version.replace('_','').replace('-','').isalnum():ap.error('simple new version required')
    root=ROOT.parent/'build/fixed_contract';final=(root/args.version).resolve();stage=(root/(args.version+'.staging')).resolve()
    if final.exists() or stage.exists():ap.error('cannot overwrite publication or staging')
    if not final.is_relative_to(root.resolve()) or not stage.is_relative_to(root.resolve()):raise ValueError('path')
    stage.mkdir(parents=True);(stage/'vectors').mkdir();(stage/'coefficients').mkdir()
    model=json.loads((args.model/'run_manifest.json').read_text(encoding='utf-8'))
    if model['status']!='complete' or model['total_frames']!=616:raise ValueError('requires full616 model')
    for rel,want in model['source_sha256'].items():
        src=args.model/'source_snapshot'/rel
        if sha(src)!=want:raise ValueError('frozen model hash '+rel)
        dst=stage/'model_snapshot'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    for rel in ['scripts/publish_fixed_full_contract.py','verification/fixed/verify_fixed_full_contract.py']:
        dst=stage/'model_snapshot'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,dst)
    sys.path.insert(0,str(stage/'model_snapshot'))
    from software.fixed_model.full_integer import preemphasis_pcm,choose_bfp_integer,quantize_fft_input,log_integer,dct_integer
    coeff_path=args.model/'coefficients.json'
    if sha(coeff_path)!=model['coefficient_sha256']:raise ValueError('coefficient hash')
    shutil.copy2(coeff_path,stage/'coefficients/coefficients.json')
    coeff=json.loads(coeff_path.read_text(encoding='utf-8'))
    def array_file(rel,values,dtype,shape=None):
        a=np.asarray(values,dtype=dtype)
        if shape is not None:a=a.reshape(shape)
        p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);a.tofile(p)
        return dict(file=rel,dtype=dtype,shape=list(a.shape),sha256=sha(p),bytes=p.stat().st_size)
    coeff_files={k:array_file('coefficients/'+k+'.bin',coeff[k],'<u4' if k!='dct_q30' else '<i4') for k in ['window_q30','mel_q16','dct_q30']}
    rom=stage/'model_snapshot/software/fixed_model/coefficients/twiddle_1024_w16.mem'
    shutil.copy2(rom,stage/'coefficients/twiddle_1024_w16.mem')
    coeff_files['twiddle_q15']=dict(file='coefficients/twiddle_1024_w16.mem',encoding='ASCII one hex32 word per line',words=1024,
        word_W=32,component_W=16,component_F=15,component_signed=True,layout='real[31:16], imaginary[15:0]',bytes=rom.stat().st_size,sha256=sha(rom))
    stage_types={'frame_starts':'<u4','preemphasis_q30':'<i4','windowed_q30':'<i4','shift_s':'<i4',
       'fft_input':'<i4','fft_re':'<i4','fft_im':'<i4','power_u40':'<u8','mel_u60':'<u8','mel_exponent':'<i4',
       'log_q24':'<i4','floor':'<u4','mfcc_q24':'<i8','fft_overflow':'<u4','input_clips':'<u4','bfp_clamped':'<u4'}
    cases=[]
    for c in model['cases']:
        src=args.model/c['integer_file'];pcmp=args.model/'arrays'/c['pcm']['file']
        if sha(src)!=c['sha256'] or sha(pcmp)!=c['pcm']['sha256']:raise ValueError('case hash')
        pcm=np.fromfile(pcmp,dtype='<i2')
        item=dict(id=c['id'],group=c['group'],frames=c['frames'],pcm=array_file('vectors/'+c['id']+'/pcm.bin',pcm,'<i2'),stages={})
        with np.load(src,allow_pickle=False) as arrays:
            for key,dtype in stage_types.items():item['stages'][key]=array_file('vectors/'+c['id']+'/'+key+'.bin',arrays[key],dtype)
        cases.append(item)
    floor=Fraction(1e-12);logs=[];bfp=[]
    for s in range(-2,25):
        exponent=-43-2*s;threshold=floor/(Fraction(2)**exponent);edge=-(-threshold.numerator//threshold.denominator)
        for value in sorted({0,max(0,edge-1),edge,edge+1,1,(1<<31)-1,1<<31,(1<<60)-1}):
            value=min(value,(1<<60)-1);answer,isfloor=log_integer(value,exponent)
            logs.append(dict(T=value,s=s,exponent=exponent,expected=answer,floor=isfloor))
        edge=(31949 << (16-s)) if s<=16 else (31949 >> (s-16))
        for peak in sorted({max(0,edge-1),edge,edge+1}):
            if peak>(1<<31)-1:continue
            selected,clamped=choose_bfp_integer([peak,-peak]);q,clips=quantize_fft_input([peak,-peak],selected)
            bfp.append(dict(peak_q30=peak,expected_s=selected,bfp_clamped=clamped,expected_q=q,clips=clips))
    pcm=[0,-32768,32767,1,-1,32767,-32768,0]
    dct_cases=[]
    for row in [[-463571610]*26,[0]*26,[-463571610 if k%2 else 244206403 for k in range(26)]]:
        dct_cases.append(dict(log_q24=row,expected=dct_integer(row,coeff['dct_q30'])))
    dump(stage/'vectors/boundaries.json',dict(log=logs,bfp=bfp,preemphasis=dict(pcm=pcm,expected=preemphasis_pcm(pcm)),dct=dct_cases,negative_energy='ERROR',invalid_s='ERROR',floor_rational=dict(numerator=floor.numerator,denominator=floor.denominator)))
    spectral=json.loads((args.spectral_contract/'contract.json').read_text(encoding='utf-8'))
    dump(stage/'spectral_contract.json',spectral)
    mel_contract=copy.deepcopy(spectral['mel'])
    mel_contract['coefficients']=copy.deepcopy(coeff_files['mel_q16'])
    fft_contract=copy.deepcopy(spectral['fft'])
    fft_contract.update(twiddle_signed=True,stage1_signed=True,stage2_signed=True,twiddle_product_signed=True,twiddle_accumulator_signed=True,
        input_physical_exp2='-14-s',output_physical_exp2='-9-s',physical_units='Unscaled windowed PCM and unnormalized DFT respectively; nominal F and BFP correction are separate')
    shutil.copy2(args.spectral_contract/'vectors/boundaries.json',stage/'vectors/spectral_boundaries.json')
    dump(stage/'numerical_evidence.json',model)
    contract=dict(schema='mfcc-fixed-contract-2',version=args.version,
        status=dict(c_portable_contract='VERIFIED_PCM_TO_MFCC',numerical_accuracy='NOT_ACCEPTED',rtl_verification='NOT_YET_VERIFIED_at_publication'),
        model_entry='software.fixed_model.full_integer.run_pcm(pcm,coefficients,twiddle)',
        source_run=dict(path=str(args.model.resolve()),sha256=sha(args.model/'run_manifest.json')),
        predecessor=dict(path=str(args.spectral_contract.resolve()),contract_sha256=sha(args.spectral_contract/'contract.json'),publication_sha256=sha(args.spectral_contract/'PUBLISHED.json')),
        preserved_definition=dict(sample_rate=16000,nfft=512,hop=160,mel=26,mfcc=13,full_frames_only=True,preemphasis='19/20',log_floor='reference binary64 1e-12',no_lifter=True),
        front=dict(pcm=dict(W=16,F=15,signed=True),preemphasis=dict(W=32,F=30,signed=True,operation='RNE((20*x-19*previous_pcm)*2**15 /20)',numerator_W=22,numerator_F=15,numerator_signed=True,shifted_numerator_W=37,shifted_numerator_F=30,shifted_numerator_signed=True,overflow='ERROR',previous_pcm='zero at clip start; update every PCM including overlap and discarded tail'),
          window=dict(input_W=32,input_F=30,input_signed=True,coefficient_W=31,coefficient_F=30,coefficient_signed=False,product_W=63,product_F=60,product_signed=True,rtl_product_W=64,output_W=32,output_F=30,output_signed=True,operation='RNE(preemphasis_q30*window_q30/2**30)',overflow='ERROR'),
          bfp=dict(s_min=-2,s_max=24,silence=0,rule='largest s with peak_q30*2**(s-16)<=31949 BEFORE rounding; clamp search only at limits',quantization='RNE(window_q30/2**(16-s)); clamp[-32767,32767]',metadata='signed8 s; frame uint32; FFT index0..511 last511',
            bfp_clamped='false for silence; true whenever selected s==24, or if no candidate fits and s=-2 is returned; otherwise false',input_clips='count of FFT input codes changed by the symmetric clamp'),
          fft=fft_contract,power=spectral['power'],mel=mel_contract),
        log=dict(input_W=60,input_signed=False,input_exp2='-43-2*s',output_W=30,output_F=24,output_signed=True,floor_rational=dict(numerator=floor.numerator,denominator=floor.denominator),floor_q24=-463571610,
          floor_test='T*2**exp < exact floor; decimal rational integer test equivalent on all27 reachable grids; negative energy ERROR',
          operation='lead=floor(log2(T)); normalize Q31 by truncate;30 iterations square unsigned64 >>31, if >=2**32 then >>1 and append1; log2_q30=((lead+exp)<<30)+fraction; RNE(log2_q30*744261118/2**36)',mantissa_W=32,mantissa_F=31,mantissa_signed=False,square_W=64,square_F=62,square_signed=False,
          log2_W=37,log2_F=30,log2_signed=True,ln2_W=30,ln2_F=30,ln2_signed=False,ln2_int=744261118,product_W=67,product_F=60,product_signed=True,
          rtl_guard_widths=dict(log2_W=40,ln2_W=31,product_W=71,all_signed=True,operation='Exact sign/zero extension only; same single RNE >>36'),overflow='ERROR; no runtime float'),
        dct=dict(input_W=30,input_F=24,input_signed=True,coefficient_W=31,coefficient_F=30,coefficient_signed=True,product_W=61,product_F=54,product_signed=True,accumulator_W=64,accumulator_F=54,accumulator_signed=True,output_W=40,output_F=24,output_signed=True,order='for c0..12 sum m0..25 exact signed products, then one RNE >>30',tight_abs_bound=model['dct_accumulator_tight_abs_bound'],overflow='ERROR; no wrap or saturation'),
        rounding='nearest ties even for every declared RNE, including negatives; unsigned masks for defined C wrapping',
        storage='headerless little endian row-major; dtype storage separate from logical W/F; empty vectors retained',
        framing=dict(clip_start='resets previous PCM and frame_id to0',frame_id='local uint32 per clip',fft_last=511,power_last=256,mel_log_last=25,mfcc_last=12,tail='discard incomplete final frame'),
        coefficients=coeff_files,cases=cases,total_frames=616,case_counts=model['case_counts'],
        spectral_reference=dict(file='spectral_contract.json',sha256=sha(stage/'spectral_contract.json'),bytes=(stage/'spectral_contract.json').stat().st_size,
            scope='Historical pinned v1 manifest; its internal relative paths resolve from predecessor.path, not this v2 directory'),
        undefined=['Numerical accuracy tolerance and application acceptance','Board clock/IO/pin integration and board execution','C implementation evidence; supplied separately by C owner'],
        accuracy=dict(summary=model['summary'],floor_regressions=sum(c['floor_regressions'] for c in model['cases']),evaluation_audio_used=False))
    if args.previous_full_contract:
        previous=args.previous_full_contract.resolve()
        publication=json.loads((previous/'PUBLISHED.json').read_text(encoding='utf-8'))
        if publication['status']!='PUBLISHED' or publication['contract_sha256']!=sha(previous/'contract.json') or publication['artifact_manifest_sha256']!=sha(previous/'artifact_hashes.json'):
            raise ValueError('previous full contract publication pins')
        prior_hashes=json.loads((previous/'artifact_hashes.json').read_text(encoding='utf-8'))
        for rel,want in prior_hashes.items():
            prior_file=(previous/rel).resolve()
            if not prior_file.is_relative_to(previous) or not prior_file.is_file() or sha(prior_file)!=want:raise ValueError('previous artifact '+rel)
        prefixes=('coefficients/','vectors/','model_snapshot/software/fixed_model/')
        comparison_files=sorted(rel for rel in prior_hashes if rel.startswith(prefixes) or rel=='numerical_evidence.json')
        compared={}
        for rel in comparison_files:
            if not (stage/rel).is_file() or sha(stage/rel)!=prior_hashes[rel]:raise ValueError('numerical content changed '+rel)
            compared[rel]=prior_hashes[rel]
        comparison=dict(status='IDENTICAL',previous_path=str(previous),previous_contract_sha256=sha(previous/'contract.json'),
            files=len(compared),scope='All prior coefficients, vectors, fixed_model source and numerical_evidence.json are byte-identical',sha256=compared)
        dump(stage/'numerical_identity.json',comparison)
        contract['supersedes']=dict(path=str(previous),contract_sha256=sha(previous/'contract.json'),change='Manifest metadata/path clarification only; no numerical change',
            numerical_identity=dict(file='numerical_identity.json',sha256=sha(stage/'numerical_identity.json'),bytes=(stage/'numerical_identity.json').stat().st_size))
    dump(stage/'contract.json',contract)
    (stage/'CONTRACT.md').write_text('''# Immutable full integer MFCC contract v2

contract.json defines PCM16 -> preemphasis32/F30 -> window32/F30 -> integer BFP
-> FFT16/F15 promoted20/F19 -> Power40 -> Mel60 -> log30/F24 -> DCT40/F24.
All runtime arithmetic in the model snapshot is integer. Coefficients were
generated offline; their supplied integer values and SHA-256 are normative.

Every case includes raw PCM and every integer stage as headerless little-endian
arrays, including ALL512 complex FFT outputs. Read array shape/dtype/hash from
cases[].stages, and logical width/scale from the stage contracts. Continuous
preemphasis crosses frame overlap boundaries and resets at clip boundaries.
The final incomplete frame is discarded; zero-frame cases are retained.

Port signed division with explicit quotient/remainder ties-even. Do not assume
implementation-defined right shift of negative C integers. FFT wraps explicitly
at its declared stage widths; the other stage bounds must be checked. log uses
64-bit unsigned squares and a signed67-bit product contract, so a C port may
use a proved split product or wider temporary rather than overflowing int64.
The log manifest distinguishes unsigned30/F30 ln2, signed37/F30 log2 and
signed67/F60 product from lossless signed40/31/71 RTL guard temporaries.
For C without int128, let a=log2_q30 and L=744261118. Use floor division:
h=floor(a/64), l=a-64*h, p=l*L, b=h*L+floor(p/64),
q=floor(b/2^30), r=b-q*2^30. Increment q when r>2^29 or when
r==2^29 and (p mod64 is nonzero or q is odd). This exactly equals the
wide-product RNE(a*L/2^36), including negative ties. All these partial
products fit signed64 for valid log inputs. C truncating division must be
corrected to floor division when its remainder is negative. Signed negative
left shift is not portable C; express scaling with checked wide multiplication.
All coefficient file descriptors resolve inside this bundle. The separate
spectral_contract.json is a historical v1 document whose internal relative
paths resolve from predecessor.path. It is not a v2 vector-file inventory.

Numerical accuracy is NOT ACCEPTED: synthetic error/floor regressions remain;
portability and bit agreement do not establish accuracy. This version records
the full integer candidate, without changing the common MFCC definition, PCM,
log floor, FFT width choice, or evaluation set. RTL evidence is versioned apart.

Publication requires PUBLISHED.json plus matching artifact_hashes.json and
verification.json PASS. Never modify a published directory. The predecessor
contract path/hash pins extra signed ties, complex overflow, recovery and FFT
boundary vectors. This full contract adds exact integer BFP and log floor edges.
''',encoding='utf-8')
    dump(stage/'artifact_hashes.json',{p.relative_to(stage).as_posix():sha(p) for p in stage.rglob('*') if p.is_file()})
    result=subprocess.run([sys.executable,'-B',str(ROOT/'verification/fixed/verify_fixed_full_contract.py'),str(stage),'--prepublish'],capture_output=True,text=True)
    (stage/'verification.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('verification failed; staging preserved')
    verification=json.loads(result.stdout)
    if verification['status']!='PASS':raise RuntimeError('missing PASS')
    dump(stage/'verification.json',verification)
    dump(stage/'artifact_hashes.json',{p.relative_to(stage).as_posix():sha(p) for p in stage.rglob('*') if p.is_file() and p.name!='artifact_hashes.json'})
    dump(stage/'PUBLISHED.json',dict(status='PUBLISHED',version=args.version,published_at=datetime.now(timezone.utc).isoformat(),contract_sha256=sha(stage/'contract.json'),artifact_manifest_sha256=sha(stage/'artifact_hashes.json'),verification_sha256=sha(stage/'verification.json')))
    stage.rename(final);print(json.dumps(dict(status='PUBLISHED',path=str(final),verification=verification),indent=2))
if __name__=='__main__':main()
