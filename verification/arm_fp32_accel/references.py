"""Frozen development-only FP32 references. No device access or evaluation data."""
from pathlib import Path
import hashlib
import json
import math
import struct

BUILD = Path(__file__).resolve().parents[3] / 'build'
GOLD = BUILD / 'fp32_hw/continuous_development_01'
GOLD_INDEX_SHA = 'ecc510646d9c75c496f4850b09a9943fa9df78f099d67b7ff5e639b8c647d7ac'
GOLD_MFCC_SHA = '591032615feeef9c84de6858af8df1d5a994b001a6535548a33502b28a57d821'
BACKEND_SHA = 'c556a8e85f30b4914ef23c523017a719be2ffc752499be7e25a4ae549201ab6b'
PCM_SHA = '026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32'
TOLERANCE_SHA = '64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6'
KNOWN_SYNTHETIC_FAILURES = ['composite_500_2237hz', 'dc_negative_8192', 'dc_positive_8192',
                            'fullscale_alternating', 'tone_bin32_1000hz']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def need(condition, message):
    if not condition:
        raise ValueError(message)


def load(gold=GOLD):
    gold = Path(gold).resolve()
    index_path = gold/'artifact_manifest.json'
    need(sha(index_path) == GOLD_INDEX_SHA, 'FP32 continuous simulation index changed')
    index = read(index_path)
    for relative, wanted in index.items():
        path = (gold/relative).resolve()
        need(path.is_relative_to(gold) and sha(path) == wanted, 'FP32 simulation artifact changed: '+relative)
    freeze, result = read(gold/'freeze.json'), read(gold/'run_manifest.json')
    need(result['status'] == 'passed' and freeze['selection'] == 'development'
         and not freeze['evaluation_audio_read'], 'Expected successful development-only simulation')
    need(freeze['source_hashes']['hardware/fp32/rtl/fp32_backend.sv'] == BACKEND_SHA, 'Wrong arithmetic backend')
    need(len(freeze['cases']) == 1, 'Only frozen development case allowed')
    case = freeze['cases'][0]
    need(case['id'] == '8463-294828-0037' and case['group'] == 'development'
         and case['samples'] == 85920 and case['frames'] == 534, 'Wrong development case')
    python = Path(case['python_reference']); pc = Path(case['c_reference'])
    paths = dict(pcm=python.parent/'input_s16le.pcm', python=python/'mfcc.bin', pc_c=pc/'mfcc.bin',
                 rtl=gold/'arrays'/case['id']/'mfcc.bin', tolerance=gold/'tolerances.json')
    wanted = dict(pcm=PCM_SHA, python=freeze['reference_hashes']['python/development/'+case['id']+'/comparison_raw13/mfcc.bin'],
                  pc_c=freeze['reference_hashes']['c/development/'+case['id']+'/mfcc.bin'],
                  rtl=GOLD_MFCC_SHA, tolerance=TOLERANCE_SHA)
    for name, path in paths.items():
        need(sha(path) == wanted[name], 'Frozen reference hash changed: '+name)
    arrays = read(gold/'arrays'/case['id']/'arrays.json')['arrays']['mfcc']
    need(arrays['dtype'] == '<f4' and arrays['shape'] == [534, 13] and arrays['sha256'] == GOLD_MFCC_SHA, 'RTLgold shape/type')
    tolerance = read(paths['tolerance'])['stages']['mfcc']
    need(tolerance == dict(atol=1e-3, rtol=1e-5), 'No tolerance relaxation permitted')
    pcm = paths['pcm'].read_bytes()
    rtl = paths['rtl'].read_bytes()
    need(len(pcm) == 171840 and len(rtl) == 27768, 'PCM/RTLgold byte counts')
    bits = struct.unpack('<6942I', rtl)
    need(all((v & 0x7f800000) != 0x7f800000 for v in bits), 'Nonfinite RTLgold')
    python_values = struct.unpack('<6942d', paths['python'].read_bytes())
    pc_values = struct.unpack('<6942f', paths['pc_c'].read_bytes())
    metadata = dict(case=case['id'], samples=85920, frames=534, records=6942, gold=str(gold),
                    gold_index_sha256=GOLD_INDEX_SHA, gold_artifact_hashes_verified=len(index), backend_sha256=BACKEND_SHA,
                    sources={name: dict(path=str(path), sha256=wanted[name]) for name, path in paths.items()},
                    tolerance=tolerance, known_synthetic_failures=KNOWN_SYNTHETIC_FAILURES,
                    synthetic_cases_retested=False, evaluation_audio_read=False,
                    scope='Frozen development speech only; known synthetic failures preserved')
    return dict(metadata=metadata, pcm=pcm, rtl=rtl, bits=bits, python=python_values, pc_c=pc_values,
                records=b''.join(struct.pack('<QIIiI', value, i//13, i%13, 0, int(i%13 == 12)) for i, value in enumerate(bits)))


def metric(values, reference, tolerance):
    need(len(values) == len(reference), 'Numeric reference length mismatch')
    errors = [abs(a-b) for a, b in zip(values, reference)]
    bad = [i for i, (a, b, e) in enumerate(zip(values, reference, errors))
           if not math.isfinite(a) or not math.isfinite(b) or e > tolerance['atol']+tolerance['rtol']*abs(b)]
    worst = max(range(len(errors)), key=errors.__getitem__) if errors else None
    return dict(passed=not bad, elements=len(values), violations=len(bad),
                nonfinite=sum(not math.isfinite(v) for v in values),
                max_abs_error=max(errors, default=0.0), rmse=math.sqrt(sum(e*e for e in errors)/len(errors)) if errors else 0.0,
                first_violation_index=list(divmod(bad[0], 13)) if bad else None,
                worst_index=list(divmod(worst, 13)) if worst is not None else None, tolerance=tolerance)


def numeric(data, bundle):
    need(len(data) % 24 == 0, 'Incomplete FP32 record')
    words = [row[0] for row in struct.iter_unpack('<QIIiI', data)]
    need(len(words) in (13, 6942), 'Only smoke and full development numeric comparison supported')
    need(all(v <= 0xffffffff for v in words), 'FP32 result high32 must be zero')
    values = struct.unpack('<'+str(len(words))+'f', struct.pack('<'+str(len(words))+'I', *words))
    tolerance = bundle['metadata']['tolerance']
    metrics = {name: metric(values, bundle[name][:len(words)], tolerance) for name in ('python', 'pc_c')}
    return dict(passed=all(m['passed'] for m in metrics.values()), comparisons=metrics,
                scope='Development speech only', known_synthetic_failures=KNOWN_SYNTHETIC_FAILURES,
                synthetic_cases_retested=False, evaluation_audio_read=False)
