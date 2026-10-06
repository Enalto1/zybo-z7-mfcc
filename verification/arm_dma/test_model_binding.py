"""Read-only real-model validation plus corruption tests in a new copied fixture."""
from pathlib import Path
import argparse
import copy
import json
import shutil
import sys
sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'scripts'))
import run_board_dma as r

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--system-run',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    source=PROJECT.parent/'build/system_dma'/a.system_run
    system=r.read(source/'run_manifest.json');r.verify_simulation_model(source,system)
    a.output.mkdir(parents=True,exist_ok=False);fixture=a.output/'fixture';fixture.mkdir()
    model=copy.deepcopy(system['simulation_model']);index={};checks=[]
    for name in [*model['files'],'simulation_model/local_xsim.ini','simulation_model/scheduling_correction.diff']:
        dest=fixture/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,dest);index[name]=r.sha(dest)
    model['initfile']=str((fixture/'simulation_model/local_xsim.ini').resolve())
    model['physical_library']=str((fixture/'simulation_model/library').resolve())
    def save(m,idx):
        r.dump(fixture/'simulation_model/model.json',m);idx['simulation_model/model.json']=r.sha(fixture/'simulation_model/model.json')
        r.dump(fixture/'artifact_hashes.json',idx)
    save(model,index);r.verify_simulation_model(fixture,dict(simulation_model=model));checks.append('valid copied fixture accepted')
    def reject(name,m,idx):
        save(m,idx)
        try:r.verify_simulation_model(fixture,dict(simulation_model=m))
        except ValueError as e:checks.append(dict(test=name,rejected=True,reason=str(e)))
        else:raise AssertionError('Corrupt model accepted: '+name)
    for key,value in [('kind','stock_vendor_model'),('module','other'),('changed_request_assignments',21),
        ('arithmetic_or_synthesized_sources_changed',True),('pristine_sha256','0'*64),('patched_sha256','0'*64),
        ('initfile',str(source/'simulation_model/local_xsim.ini')),('diff_sha256','0'*64)]:
        changed=copy.deepcopy(model);changed[key]=value;reject(key,changed,copy.deepcopy(index))
    for flavor in ('pristine','patched'):
        name=f'simulation_model/{flavor}/processing_system7_vip_v1_0_vl_rfs.sv';path=fixture/name;original=path.read_bytes()
        path.write_bytes(original+b'\n// Corruption fixture only\n')
        changed=copy.deepcopy(model);changed_index=copy.deepcopy(index);digest=r.sha(path)
        changed['files'][name]=digest;changed_index[name]=digest
        reject(flavor+'_bytes_with_matching_manifest_and_index',changed,changed_index)
        path.write_bytes(original)
        changed=copy.deepcopy(model);changed['files'].pop(name);reject(flavor+'_omitted_from_model',changed,copy.deepcopy(index))
        changed_index=copy.deepcopy(index);changed_index.pop(name);reject(flavor+'_omitted_from_index',copy.deepcopy(model),changed_index)
    for name in ('simulation_model/local_xsim.ini','simulation_model/scheduling_correction.diff'):
        path=fixture/name;original=path.read_bytes();path.write_bytes(original+b'\nCORRUPTED_FIXTURE\n')
        reject(name+'_bytes',copy.deepcopy(model),copy.deepcopy(index));path.write_bytes(original)
    save(model,index);r.verify_simulation_model(fixture,dict(simulation_model=model))
    for source_file in [Path(__file__),PROJECT/'scripts/run_board_dma.py',PROJECT/'scripts/build_arm_dma.py']:
        shutil.copy2(source_file,a.output/source_file.name)
    result=dict(passed=True,checks=checks,check_count=len(checks),system_run=str(source),
        system_manifest_sha256=r.sha(source/'run_manifest.json'),runner_sha256=r.sha(PROJECT/'scripts/run_board_dma.py'),
        builder_sha256=r.sha(PROJECT/'scripts/build_arm_dma.py'),board_accessed=False,xsct_used=False,
        historical_model_untouched=True)
    r.dump(a.output/'results.json',result)
    r.dump(a.output/'artifact_hashes.json',{f.relative_to(a.output).as_posix():r.sha(f) for f in a.output.rglob('*') if f.is_file()})
    print('PASS MODEL_BINDING checks='+str(len(checks)))
if __name__=='__main__':main()
