"""Publish immutable, checked integer contract bundles for independent C/RTL ports."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from software.fixed_model.contract import spectral_frame
from software.fixed_model.fft_bitmodel import load_twiddle_rom
from software.fixed_model.coeffs import quantize_mel_filterbank
from software.fixed_model.qnum import round_half_even

DEFAULT_STUDY = ROOT.parent / 'build/fft_precision/prec_04_verified_full_20261004'
DEFAULT_OUT = ROOT.parent / 'build/fixed_contract'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def dump_array(base, name, rows, dtype, columns):
    values = [int(v) for row in rows for v in row]
    limits = np.iinfo(dtype)
    if any(v < limits.min or v > limits.max for v in values):
        raise OverflowError(f'{name}: storage would narrow an integer')
    array = np.asarray(values, dtype=dtype).reshape(len(rows), columns)
    path = base / name
    array.tofile(path)
    return dict(file=path.relative_to(base.parent).as_posix(), dtype=dtype,
                shape=list(array.shape), bytes=path.stat().st_size, sha256=sha(path))


def directed_inputs(twiddle, weights):
    result = []
    def add(name, re, im=None, s=0):
        result.append((name, list(re), list(im) if im is not None else [0]*512, s))
    for amp in (1, -1, 16, -16, 48, -48, 32767, -32768):
        add(f'impulse0_{amp}', [amp]+[0]*511)
    for amp in (16384, -16384, 32767, -32768):
        add(f'dc_{amp}', [amp]*512)
    add('imag_impulse_negative', [0]*512, [-16384]+[0]*511, 24)
    add('complex_bin128', [16384,0,-16384,0]*128, [0,16384,0,-16384]*128, -2)
    add('nyquist_extrema', [-32768,32767]*256)
    add('recovery_silence', [0]*512)
    # Find and retain a concrete wrapped overflow case, then a clean recovery.
    rng = np.random.default_rng(7)
    for probe in range(400):
        re = np.where(rng.random(512)<0.5, -32768, 32767).tolist()
        im = np.where(rng.random(512)<0.5, -32768, 32767).tolist()
        if spectral_frame(re, im, 0, twiddle, weights)['fft_overflow']:
            add(f'complex_overflow_seed7_probe{probe}', re, im)
            add('overflow_recovery_half', [v//2 for v in re], [v//2 for v in im])
            break
    else:
        raise AssertionError('directed suite lacks an observed FFT overflow')
    return result


def boundary_vectors():
    rounding = []
    for shift in (1,2,15,30):
        unit = 1 << shift
        for whole in (-3,-2,-1,0,1,2,3):
            value = whole*unit + unit//2
            expected = whole if whole%2 == 0 else whole+1
            rounding.append(dict(value=value, shift=shift, expected=expected))
    floor = Fraction(1e-12)
    floors = []
    for s in (-2,0,1,10,24):
        exponent = -43-2*s
        threshold = floor / (Fraction(2)**exponent)
        ceil_threshold = -(-threshold.numerator//threshold.denominator)
        for value in sorted({0,max(0,ceil_threshold-1),ceil_threshold,ceil_threshold+1}):
            floors.append(dict(T=value,bfp_s=s,mel_exp2=exponent,
                               below_floor=bool(value*(Fraction(2)**exponent)<floor)))
    bfp = []
    # Exact rational pre-quantization peaks either side of every decision edge.
    for wanted_s in (-2,0,1,10,23,24):
        boundary = Fraction(31949,32768) / (Fraction(2)**(wanted_s-1))
        for delta in (Fraction(-1,1<<45),Fraction(0),Fraction(1,1<<45)):
            peak = boundary+delta
            choices = [s for s in range(-2,25) if peak*Fraction(2)**(s-1)*32768 <= 31949]
            s = max(choices) if choices else -2
            bfp.append(dict(peak_num=peak.numerator,peak_den=peak.denominator,
                            expected_s=s,target_int=31949,rule='unrounded'))
    return dict(rounding=rounding,floor=floors,bfp=bfp,
                floor_rational=dict(numerator=floor.numerator,denominator=floor.denominator),
                invalid_energy=[dict(T=-1,expected='ERROR: never floor negative energy')],
                signed20_boundaries=[dict(value=v,wrapped=((v+(1<<19))%(1<<20))-(1<<19),
                                          overflow=not(-(1<<19)<=v<(1<<19)))
                                     for v in (-(1<<19)-1,-(1<<19),-1,0,(1<<19)-1,1<<19)])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--version',required=True)
    ap.add_argument('--study',type=Path,default=DEFAULT_STUDY)
    ap.add_argument('--out-root',type=Path,default=DEFAULT_OUT)
    args=ap.parse_args()
    if not args.version or Path(args.version).name!=args.version or args.version in ('.','..'):
        ap.error('version must be one new directory name')
    base=args.out_root.resolve();base.mkdir(parents=True,exist_ok=True)
    final=(base/args.version).resolve(); staging=(base/(args.version+'.staging')).resolve()
    if not final.is_relative_to(base) or not staging.is_relative_to(base):
        raise RuntimeError('publication paths must stay in the contract root')
    if final.exists() or staging.exists():
        ap.error('existing publication/staging is immutable; choose a new version')
    staging.mkdir();(staging/'vectors').mkdir();(staging/'coefficients').mkdir()
    sources=['software/fixed_model/__init__.py','software/fixed_model/contract.py',
             'software/fixed_model/qnum.py','software/fixed_model/fft_bitmodel.py',
             'software/fixed_model/fft_bitmodel_wide.py','software/fixed_model/coeffs.py',
             'software/fixed_model/power_mel.py','scripts/publish_fixed_contract.py',
             'verification/fixed/verify_fixed_contract.py']
    source_hashes={}
    for name in sources:
        dest=staging/'model_snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,dest);source_hashes[name]=sha(dest)
    study_manifest=args.study/'run_manifest.json'
    study=json.loads(study_manifest.read_text(encoding='utf-8'))
    if study['status']!='completed' or study['input_counts']!={'development':1,'synthetic':17,'extra_synthetic':6}:
        raise ValueError('requires the complete 24-input precision study')
    table=quantize_mel_filterbank(16);weights=table.weights_int
    rom=ROOT/'software/fixed_model/coefficients/twiddle_1024_w16.mem'
    twiddle=load_twiddle_rom(str(rom))
    shutil.copy2(rom,staging/'coefficients/twiddle_1024_w16.mem')
    write_json(staging/'coefficients/mel_u17_f16.json',weights)
    coeff_meta=dump_array(staging/'coefficients','mel_u32le.bin',weights,'<u4',257)
    (staging/'coefficients/mel_u17_f16.mem').write_text(
        ''.join(f'{w:05x}\n' for row in weights for w in row),encoding='ascii')
    vectors={k:[] for k in ('input_real','input_imag','fft_real','fft_imag','power','mel','meta')}
    cases=[];frame_map=[]
    def append_frame(re,im,s,label,group,local_frame):
        answer=spectral_frame(re,im,int(s),twiddle,weights)
        fid=len(frame_map)
        for name,row in [('input_real',re),('input_imag',im),('fft_real',answer['fft_real']),
                         ('fft_imag',answer['fft_imag']),('power',answer['power']),('mel',answer['mel'])]:
            vectors[name].append(row)
        vectors['meta'].append([fid,int(s),int(answer['fft_overflow']),answer['power_exp2'],answer['mel_exp2']])
        frame_map.append(dict(frame_id=fid,input_id=label,group=group,input_frame=local_frame,
                              start_sample=local_frame*160 if group!='directed' else None))
        return answer
    for case in study['part4_width_results']:
        row=case['targets']['t0p975']['d20_out20_in16'];count=case['frames'];start=len(frame_map)
        arrays={}
        for name in ('input_q','shift','fft_re','fft_im','psum','mel'):
            meta=row['integer_dumps'][name];path=args.study/'arrays'/meta['file']
            if sha(path)!=meta['sha256']:raise ValueError(f'study hash mismatch: {path}')
            arrays[name]=np.fromfile(path,dtype=meta['format']).reshape(meta['shape'])
        for n in range(count):
            answer=append_frame(arrays['input_q'][n].tolist(),[0]*512,int(arrays['shift'][n,0]),
                                case['id'],case['group'],n)
            for key,old in [('fft_real','fft_re'),('fft_imag','fft_im'),('power','psum'),('mel','mel')]:
                if answer[key][:len(arrays[old][n])]!=arrays[old][n].tolist():
                    raise AssertionError(f'published model disagrees with study {case["id"]}/{n}/{key}')
        original=next(c for c in study['inputs'] if c['id']==case['id'])
        pcm_src=args.study/'arrays'/original['pcm']['file']
        if sha(pcm_src)!=original['pcm']['sha256']:raise ValueError('PCM hash mismatch')
        pcm_dest=staging/'vectors'/original['pcm']['file'];shutil.copy2(pcm_src,pcm_dest)
        cases.append(dict(input_id=case['id'],group=case['group'],frames=count,
                          first_frame=start,pcm=pcm_dest.relative_to(staging).as_posix(),
                          pcm_sha256=sha(pcm_dest),samples=original['pcm']['samples']))
        print(f'contract vectors {case["id"]}: {count}',flush=True)
    normal_count=len(frame_map)
    for name,re,im,s in directed_inputs(twiddle,weights):
        append_frame(re,im,s,name,'directed',0)
    files={name:dump_array(staging/'vectors',name+'.bin',rows,
                          '<u8' if name in ('power','mel') else '<i4',
                          257 if name=='power' else 26 if name=='mel' else 5 if name=='meta' else 512)
           for name,rows in vectors.items()}
    write_json(staging/'vectors/frame_map.json',frame_map)
    write_json(staging/'vectors/boundaries.json',boundary_vectors())
    manifest=dict(schema='mfcc-fixed-contract-1',version=args.version,
        status=dict(c_portable_contract='verified_fft_power_mel',numerical_accuracy='NOT_ACCEPTED_tolerance_unset',
                    rtl_verification='NOT_YET_VERIFIED_at_publication',full_integer_mfcc='NOT_INCLUDED'),
        spec_id='mfcc-raw13-v0.1-draft',fft=dict(n=512,input_W=16,input_F=15,input_signed=True,
        input_promotion_left=4,internal_W=20,internal_F=19,output_W=20,output_F=19,
        twiddle_W=16,twiddle_F=15,stage1_W=21,stage2_W=22,twiddle_product_W=38,
        twiddle_accumulator_W=39,rounding='nearest_ties_even_at_twiddle_>>15_group_>>2_trailing_>>1',
        physical_shift_S=9,output_requant_shift=0,overflow='two_complement_wrap_sticky_per_frame',
        output_order='natural_k0_to511_all_complex_bins',stages='four radix2^2 groups then one radix2',
        model_entry='software.fixed_model.contract.spectral_frame'),
        bfp=dict(s_min=-2,s_max=24,silence_s=0,target_int=31949,input_F=15,
        selection='largest s satisfying unrounded peak*2**(s-1)*2**15<=31949',
        input_quantization='nearest_ties_even_then_signed16_saturate_then_symmetric_clamp_-32767_to32767',
        scope='v1 consumes already quantized input and s; integer PCM/front-end selection deferred'),
        power=dict(input_signed_W=20,square_full_signed_W=40,output_unsigned_W=40,
        bins=257,order='re*re + im*im unlimited precision then unsigned40 bound check',
        exp2='-27-2*s',rounding='none',overflow='ERROR'),
        mel=dict(weight_unsigned_W=17,weight_F=16,product_full_unsigned_W=57,
        accumulator_unsigned_W=60,exp2='-43-2*s',rounding='none_after_coefficient_generation',
        order='for each m=0..25 sum bins k=0..256, exact integer products',
        overflow='ERROR',per_band_weight_sum=[sum(r) for r in weights],coefficients=coeff_meta),
        floor=dict(value_text='1e-12',**boundary_vectors()['floor_rational'],
        application='compare T*2**(-43-2*s) in common energy units; negative is ERROR',
        integer_log='not_in_v1'),
        framing=dict(samples=512,hop=160,full_frames_only=True,frame_id='uint32 metadata',
                     bfp_s='signed8 metadata held for the entire frame',
                     last='FFT bin511; power bin256; Mel band25'),
        protocol=dict(core_output_ready=False,core_global_stall=False,
        rule='reserve frame storage before first input transfer; output may not be stalled',
        c_api='one complete frame per call; all512 outputs and one overflow flag',
        reset='each call clears FFT overflow; PCM preemphasis state not part of v1'),
        storage='headerless little-endian C row-major; logical W independent of int32/uint64 storage',
        vector_files=files,cases=cases,normal_frames=normal_count,total_frames=len(frame_map),
        stress_overflow_frames=sum(v[2] for v in vectors['meta'][normal_count:]),
        prior_study=dict(path=str(args.study.resolve()),manifest_sha256=sha(study_manifest)),
        source_sha256=source_hashes,model_preserved_sha256=sha(ROOT/'software/fixed_model/fft_bitmodel.py'),
        original_twiddle_sha256=sha(rom),
        undefined=['integer PCM/preemphasis/window','integer log and DCT formats','accuracy acceptance limits',
                   'RTL resources/timing; board performance and AI accuracy'])
    write_json(staging/'contract.json',manifest)
    text='''# Fixed MFCC contract v1: FFT20 -> Power40 -> Mel60

This bundle is portable integer C/RTL input, not a numerical accuracy pass.
The contract.json machine contract is normative. The source snapshot and
coefficient files pin every rounding/wrap decision. All512 complex FFT bins
are supplied, then257 unsigned40 power bins and26 unsigned60 Mel accumulators.
Read vectors/meta.bin as [frame_id,s,overflow,power_exp2,mel_exp2] int32 rows.
All other binary array shape/dtype/SHA are in contract.json/vector_files.
Normal616 frames are all development534 and synthetic82 frames, in case order;
zero-frame inputs remain in cases. Directed vectors follow the normal rows.
Frame_map.json distinguishes stress inputs which intentionally bypass symmetric
input clamp. C storage types do not replace logical signed20/wrap behavior.

FFT group: Type-I W21; Type-II W22; signed16/F15 twiddle; full signed38 products
and signed39 add/sub; ties-even >>15 toW22, then ties-even >>2 toW20. Repeat four
groups; trailing Type-I W21 then ties-even >>1 toW20. Input q16 promotes by*16.
Physical FFT normalization is /512, output F19. Natural order includes k511.
Wrap at declared boundaries, per-frame sticky overflow; no silent saturation.
Power/Mel multiply and sum exactly, error on out-of-range rather than wrap.
Negative right shifts in C must not rely on implementation-defined semantics:
round signed division mathematically nearest/even (quotient+remainder), then
perform explicitly unsigned-mask two's-complement wrapping and sign decoding.

BFP selection uses the unrounded peak rule. v1 inputs are already q16+s;
preprocessing/window and integer log/DCT remain explicitly undefined here.
The floor is the recorded exact rational representation of reference1e-12.
Do not replace it with testing T==0. See vectors/boundaries.json for exact
negative ties, signed bounds, BFP edge and floor edge expectations.

Publication requires verification.json PASS, artifact_hashes.json and the
PUBLISHED.json commit marker. Existing published directories are immutable.
Later RTL evidence or a full integer front-end must be a new version/evidence
record, never edits to this bundle. Board execution is not authorized here.
'''
    (staging/'CONTRACT.md').write_text(text,encoding='utf-8')
    write_json(staging/'artifact_hashes.json',{p.relative_to(staging).as_posix():sha(p)
               for p in sorted(staging.rglob('*')) if p.is_file()})
    # Validation is a separate implementation; only publish after it succeeds.
    import subprocess
    result=subprocess.run([sys.executable,'-B',str(ROOT/'verification/fixed/verify_fixed_contract.py'),
                           str(staging),'--prepublish'],capture_output=True,text=True)
    (staging/'verification.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('contract verification failed; staging preserved')
    verification=json.loads(result.stdout)
    if verification['status']!='PASS':raise RuntimeError('verifier did not pass')
    write_json(staging/'verification.json',verification)
    write_json(staging/'artifact_hashes.json',{p.relative_to(staging).as_posix():sha(p)
               for p in sorted(staging.rglob('*')) if p.is_file() and p.name!='artifact_hashes.json'})
    write_json(staging/'PUBLISHED.json',dict(status='PUBLISHED',version=args.version,
        published_at=datetime.now(timezone.utc).isoformat(),contract_sha256=sha(staging/'contract.json'),
        artifact_manifest_sha256=sha(staging/'artifact_hashes.json'),verification_sha256=sha(staging/'verification.json')))
    # Both resolved paths were checked to stay under the exact publication root.
    staging.rename(final)
    print(json.dumps(dict(status='PUBLISHED',path=str(final),normal_frames=normal_count,
                          total_frames=len(frame_map),verification=verification),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
