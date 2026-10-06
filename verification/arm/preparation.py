"""Validate immutable baselines and create an execution plan without board claims."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from protocol import frame_count, crc32


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def dump(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def verify_index(root: Path, expected_manifest: str, expected_index: str) -> dict:
    if sha(root/'run_manifest.json') != expected_manifest or sha(root/'artifact_manifest.json') != expected_index:
        raise ValueError('Pinned run/index identity changed: ' + str(root))
    index = read(root/'artifact_manifest.json')
    for relative, digest in index.items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha(path) != digest:
            raise ValueError('Pinned artifact mismatch: ' + str(path))
    return index


def prepare(project: Path) -> dict:
    pins = read(project/'verification/arm/baseline_pins.json')
    py, pc = Path(pins['python_root']), Path(pins['c_root'])
    indices = {key: verify_index(root,pins[key+'_run_manifest_sha256'],pins[key+'_artifact_manifest_sha256'])
        for key, root in [('python',py),('c',pc)]}
    pc_manifest = read(pc/'run_manifest.json')
    for rel in ('software/c/mfcc.c','software/c/mfcc.h','software/c/fft32.c','software/c/fft32.h',
                'verification/c/compare.py','verification/c/tolerances.json','verification/c/known_development_limits.json'):
        if sha(project/rel) != pc_manifest['source_hashes'][rel]:
            raise ValueError('Frozen common core/check/tolerance source changed: ' + rel)
    cases=[]
    for role in ('synthetic','development','evaluation'):
        for folder in sorted((py/role).iterdir()):
            if not folder.is_dir():
                continue
            validation=read(folder/'validation.json')
            if validation['passed'] is not True or validation['profiles']['comparison_raw13']['passed'] is not True:
                raise ValueError('Python reference validation failed: '+folder.name)
            pcm=folder/'input_s16le.pcm'
            raw=pcm.read_bytes()
            count=len(raw)//2
            expected_frames=frame_count(count)
            if len(raw)%2 or sha(pcm)!=validation['pcm_sha256'] or validation['sample_count']!=count:
                raise ValueError('Pinned PCM metadata mismatch: '+folder.name)
            host=read(pc/role/folder.name/'host_manifest.json')
            if host.get('status')!='passed' or host.get('sample_count')!=count or host.get('frame_count')!=expected_frames:
                raise ValueError('PC host structure differs from pinned PCM: '+folder.name)
            cases.append(dict(id=folder.name,role=role,pcm=str(pcm),pcm_sha256=sha(pcm),
                pcm_crc32=crc32(raw),sample_count=count,frame_count=expected_frames,
                python_directory=str(folder/'comparison_raw13'),c_directory=str(pc/role/folder.name)))
    for role, expected_clips, expected_frames in [('synthetic',17,40),('development',1,534),('evaluation',20,9501)]:
        selected=[x for x in cases if x['role']==role]
        if len(selected)!=expected_clips or sum(x['frame_count'] for x in selected)!=expected_frames:
            raise ValueError('Pinned dataset count mismatch: '+role)
    development=next(x for x in cases if x['role']=='development')
    if development['id']!=pins['development_clip'] or development['sample_count']!=85920:
        raise ValueError('Development identity changed')
    return dict(schema_version=1,status='prepared_unverified_on_arm',pins=pins,
        checked_artifacts={k:len(v) for k,v in indices.items()},cases=cases,
        tolerance_sha256=sha(project/'verification/c/tolerances.json'),
        known_limits_sha256=sha(project/'verification/c/known_development_limits.json'),
        development_gate_passed=False,evaluation_executed=False,timing_measured=False,
        limitations=['Offline integrity and protocol checks do not validate ARM numerical behavior.',
                    'PC binary32 known synthetic failures remain failures; no ARM result is presumed.'])
