"""Frozen development-only references shared by DMA build and board audit."""
from pathlib import Path
import importlib.util
import hashlib
import json
import math
import struct

PROJECT=Path(__file__).resolve().parents[2]
BUILD=PROJECT.parent/'build'
spec=importlib.util.spec_from_file_location('dma_fp32_reference',PROJECT/'verification/arm_fp32_accel/references.py')
fp32=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp32)
CONTRACT=BUILD/'fixed_contract/v2_pcm16_mfcc40_20261004_r2'
CONTRACT_SHA='283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e'
INDEX_SHA='67403d3d0483b376ab578355eb623947bba931d68c1bde6dca03cbbc32d05b45'
sha=fp32.sha
read=fp32.read
need=fp32.need

def load(variant):
    base=fp32.load()
    if variant=='fp32':
        base['metadata']['variant']='fp32'
        base['metadata']['core_id']=2
        return base
    need(variant=='fixed','Choose fixed or fp32')
    need(sha(CONTRACT/'contract.json')==CONTRACT_SHA and sha(CONTRACT/'artifact_hashes.json')==INDEX_SHA,'Published fixed contract changed')
    index=read(CONTRACT/'artifact_hashes.json')
    for name,digest in index.items():
        path=(CONTRACT/name).resolve()
        need(path.is_relative_to(CONTRACT.resolve()) and sha(path)==digest,'Fixed artifact changed: '+name)
    contract=read(CONTRACT/'contract.json')
    case=next(c for c in contract['cases'] if c['group']=='development')
    need(case['frames']==534 and case['pcm']['sha256']==fp32.PCM_SHA,'Fixed PCM identity')
    q=struct.unpack('<6942q',(CONTRACT/case['stages']['mfcc_q24']['file']).read_bytes())
    shifts=struct.unpack('<534i',(CONTRACT/case['stages']['shift_s']['file']).read_bytes())
    need(all(-(1<<39)<=v<(1<<39) for v in q) and all(-2<=v<=24 for v in shifts),'Fixed widths')
    records=b''.join(struct.pack('<qIIiI',v,i//13,i%13,shifts[i//13],int(i%13==12)) for i,v in enumerate(q))
    fixed_board=BUILD/'board_validation/board_20261005_01/fixed_01/validate/results.bin'
    need(sha(fixed_board.parents[1]/'artifact_manifest.json')=='be293865edc73bf516676dc13e029f1376e747d21eb456f3ec37cdb873886c7f','Frozen fixed C index changed')
    fixed_manifest=read(fixed_board.parents[1]/'artifact_manifest.json')
    need(sha(fixed_board)==fixed_manifest['validate/results.bin'],'Frozen fixed C board result changed')
    rows=list(struct.iter_unpack('<13q8i',fixed_board.read_bytes()))
    need(len(rows)==534 and tuple(v for row in rows for v in row[:13])==q,'Fixed C board raw13 disagreement')
    need(all(row[13]==i and row[14]==i*160 and row[15]==shifts[i] for i,row in enumerate(rows)),'Fixed C metadata disagreement')
    for name in ('fft_overflow','input_clips','bfp_clamped'):
        need(not any((CONTRACT/case['stages'][name]['file']).read_bytes()),'Published fixed diagnostic nonzero: '+name)
    base['records']=records
    base['metadata'].update(variant='fixed',core_id=1,contract_sha256=CONTRACT_SHA,
        contract_index_sha256=INDEX_SHA,contract_artifacts_verified=len(index),
        fixed_c_board=dict(path=str(fixed_board),sha256=sha(fixed_board)),numerical_accuracy_status='NOT_ACCEPTED')
    return base

def compare(data,bundle):
    expected=bundle['records'][:len(data)]
    need(len(data) in (13*24,6942*24),'Only smoke/full development records allowed')
    mismatches=[]
    for i,(a,b) in enumerate(zip(struct.iter_unpack('<QIIiI',data),struct.iter_unpack('<QIIiI',expected))):
        if a!=b:mismatches.append(dict(record=i,actual=list(a),expected=list(b)))
    if bundle['metadata']['variant']=='fp32':
        metrics=fp32.numeric(data,bundle)
        numeric_gate=metrics['passed']
    else:
        values=[row[0]/2**24 for row in struct.iter_unpack('<qIIiI',data)]
        errors=[abs(a-b) for a,b in zip(values,bundle['python'])]
        metrics=dict(numerical_accuracy_status='NOT_ACCEPTED',elements=len(values),
            max_abs_error=max(errors),rmse=math.sqrt(sum(e*e for e in errors)/len(errors)),
            tolerance_not_relaxed=True,source='same Python float64 development raw13')
        numeric_gate=True # integer-bit gate does not claim float64 accuracy acceptance
    return dict(passed=not mismatches and numeric_gate,records=len(data)//24,mismatched_records=len(mismatches),
        mismatches=mismatches,sha256=hashlib.sha256(data).hexdigest(),numeric=metrics)
