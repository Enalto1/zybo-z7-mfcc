"""ARM/PC and ARM/Python comparisons remain separate; failures are never hidden."""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'c'))
from compare import _metric, load_arrays

TRACE_ORDER=('preemphasis','frames','windowed','fft','power','mel_energies','log_mel','dct','frame_energy','mfcc')


def violation_mask(actual, reference, tolerance):
    dtype=np.complex128 if np.iscomplexobj(reference) else np.float64
    a,b=np.asarray(actual,dtype=dtype),np.asarray(reference,dtype=dtype)
    return np.abs(a-b) > tolerance['atol'] + tolerance['rtol'] * np.abs(b)


def metric(actual, reference, tolerance):
    result=_metric(actual,reference,**tolerance)
    if result.get('shape_ok') and result.get('finite'):
        mask=violation_mask(actual,reference,tolerance)
        result['violation_mask_sha256']=hashlib.sha256(mask.tobytes()).hexdigest()
    return result


def compare_stages(actual: dict, python: dict, pc: dict, tolerances: dict) -> dict:
    comparisons={}
    for label,reference in [('python',python),('pc',pc)]:
        stages={}
        for stage in TRACE_ORDER:
            if stage not in actual:
                continue
            tolerance=tolerances['stages'].get(stage,tolerances['default'])
            stages[stage]=metric(actual[stage],reference[stage],tolerance)
            if label=='pc':
                cast=np.asarray(actual[stage],dtype=reference[stage].dtype)
                stages[stage]['bit_identical']=cast.shape==reference[stage].shape and cast.tobytes()==reference[stage].tobytes()
        comparisons[label]=dict(passed=all(x['passed'] for x in stages.values()),stages=stages,
            first_failed_stage=next((k for k,v in stages.items() if not v['passed']),None),
            first_nonzero_difference_stage=next((k for k,v in stages.items() if (v.get('max_abs') or 0)>0),None),
            first_bit_different_stage=next((k for k,v in stages.items() if v.get('bit_identical') is False),None))
    return comparisons


def compare_results(records: dict, case: dict, tolerances: dict) -> dict:
    python,_=load_arrays(Path(case['python_directory']),reference=True)
    pc,_=load_arrays(Path(case['c_directory']))
    result=compare_stages({'mfcc':records['mfcc']},python,pc,tolerances)
    result['per_coefficient']={label:[metric(records['mfcc'][:,k],source['mfcc'][:,k],tolerances['stages']['mfcc'])
        for k in range(13)] for label,source in [('python',python),('pc',pc)]}
    result['coverage']='all emitted MFCC records; intermediate stages only in separate focused traces'
    result['frame_count']=len(records['mfcc'])
    return result


def trace_comparison(stages: dict, preemphasis: np.ndarray, case: dict, frame: int, tolerances: dict) -> dict:
    python,_=load_arrays(Path(case['python_directory']),reference=True)
    pc,_=load_arrays(Path(case['c_directory']))
    selected={}
    for label,source in [('python',python),('pc',pc)]:
        selected[label]={name:source[name][frame:frame+1] for name in stages}
        selected[label]['preemphasis']=source['preemphasis']
    actual={**stages,'preemphasis':preemphasis}
    result=compare_stages(actual,selected['python'],selected['pc'],tolerances)
    baseline=compare_stages(selected['pc'],selected['python'],selected['pc'],tolerances)['python']
    result['baseline_python']=baseline
    # A known case ID never excuses a new failing element or stage on ARM.
    result['same_python_violation_masks_as_pc']=all(
        result['python']['stages'][k].get('violation_mask_sha256')==v.get('violation_mask_sha256')
        for k,v in baseline['stages'].items())
    result['frame']=frame
    return result


def choose_focus_frames(records: dict, case: dict) -> list[int]:
    count=case['frame_count']
    if case['role']=='synthetic':
        return list(range(count)) if count else [0xFFFFFFFF]
    if count==0:
        return []
    python,_=load_arrays(Path(case['python_directory']),reference=True)
    pc,_=load_arrays(Path(case['c_directory']))
    frames={0}
    for source in (python,pc):
        error=np.abs(records['mfcc'].astype(np.float64)-source['mfcc'].astype(np.float64))
        if error.size:
            frames.add(int(np.unravel_index(error.argmax(),error.shape)[0]))
            differing=np.flatnonzero(np.any(error!=0,axis=1))
            if len(differing):
                frames.add(int(differing[0]))
    return sorted(frames)


def development_gate(cases: list[dict], known_limits: dict) -> dict:
    reasons=[]
    if sum(x['frame_count'] for x in cases if x['role']=='development')!=534:
        reasons.append('Development total must be exactly 534 frames')
    if sum(x['role']=='synthetic' for x in cases)!=17 or sum(x['role']=='development' for x in cases)!=1:
        reasons.append('Development requires exactly synthetic17 and speech1')
    retained=[]
    for case in cases:
        result=case['comparison']
        traces=case['traces']
        if not case.get('structural_passed') or not result['pc']['passed'] or any(not t['pc']['passed'] for t in traces):
            reasons.append(case['id']+': structural or ARM/PC numerical mismatch')
        failed={name for group in [result,*traces] for name,value in group['python']['stages'].items() if not value['passed']}
        if failed:
            allowed=set(known_limits['allowed_numerical_failure_stages'].get(case['id'],[]))
            if case['role']!='synthetic' or failed!=allowed or len(traces)!=case['frame_count'] or any(not t['same_python_violation_masks_as_pc'] for t in traces):
                reasons.append(case['id']+': new/unclassified ARM/Python failure or incomplete trace coverage')
            else:
                retained.append(dict(id=case['id'],failed_stages=sorted(failed),numerical_passed=False))
    return dict(passed=not reasons,controlled_evaluation_allowed=not reasons,reasons=reasons,
        numeric_acceptance_passed=not reasons and not retained,retained_baseline_failures=retained,
        policy='Known failures require the same stages and element violation masks as PC, plus ARM/PC tolerance pass. No evaluation failure is exempt.',
        intermediate_coverage='synthetic: all full frames; development speech: first and earliest/worst output-difference frames')
