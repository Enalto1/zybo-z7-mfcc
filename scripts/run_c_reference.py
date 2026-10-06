"""Build portable C MFCC and gate evaluation on frozen development evidence.

Uses immutable Python run arrays, never recalculates or changes the Python truth.
Generated coefficients, executables, logs and samples remain outside the repo.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
BUILD = WORKSPACE / 'build/c_reference'
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['MPLCONFIGDIR'] = str(BUILD / '_matplotlib')
sys.path.insert(0, str(PROJECT / 'verification/c'))
import numpy as np
from generate_coefficients import generate
from compare import compare_case
from plots import plot_case
from host_checks import run_host_checks
from diagnose_precision import diagnose


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def source_snapshot():
    files = [Path(__file__)]
    for group in ('software/c', 'verification/c'):
        files += [p for p in (PROJECT/group).rglob('*') if p.is_file() and p.suffix in ('.c','.h','.py','.json','.txt')]
    return {p.relative_to(PROJECT).as_posix(): sha(p) for p in sorted(files)}


def checked_process(command, cwd, log, env=None):
    result = subprocess.run([str(a) for a in command], cwd=cwd, env=env, capture_output=True, text=True, errors='replace')
    Path(log).write_text('ARGV '+json.dumps([str(a) for a in command])+'\n'+result.stdout+'\n'+result.stderr+f'\nEXIT {result.returncode}\n', encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}), see {log}')
    return result


def git(args):
    result = subprocess.run(['git','-C',str(PROJECT),*args], capture_output=True,text=True)
    return {'exit_code': result.returncode, 'stdout': result.stdout.strip(), 'stderr':result.stderr.strip()}


def compiler_environment(out, config):
    batch = out/'build'/'capture_environment.cmd'
    batch.parent.mkdir(parents=True, exist_ok=True)
    vcvars = Path(config['vcvars64'])
    if not vcvars.is_file():
        raise FileNotFoundError(f'MSVC environment script missing: {vcvars}')
    batch.write_text('@echo off\ncall "'+str(vcvars)+'" >nul\nif errorlevel 1 exit /b 1\nset\n', encoding='utf-8')
    result = subprocess.run(['cmd.exe','/d','/c',str(batch)], capture_output=True,text=True,errors='replace')
    if result.returncode:
        raise RuntimeError('MSVC environment initialization failed: '+result.stderr)
    env = os.environ.copy()
    for line in result.stdout.splitlines():
        if '=' in line and not line.startswith('='):
            name,value = line.split('=',1)
            env[name] = value
    compiler = shutil.which('cl.exe', path=env.get('Path',env.get('PATH')))
    if compiler is None:
        raise RuntimeError('cl.exe missing after vcvars64')
    # Do not allow ambient compiler-option injection to silently change the experiment.
    env.pop('CL',None)
    env.pop('_CL_',None)
    env.pop('LINK',None)
    version = subprocess.run([compiler],env=env,capture_output=True,text=True,errors='replace')
    identity = {'path':compiler,'sha256':sha(compiler),'version_output':version.stdout+version.stderr,
        'vcvars_sha256':sha(vcvars),'VCToolsVersion':env.get('VCToolsVersion'),
        'WindowsSDKVersion':env.get('WindowsSDKVersion'),'VisualStudioVersion':env.get('VisualStudioVersion'),
        'flags':config['flags'],'runtime_files':{}}
    for name in ('ucrtbase.dll','vcruntime140.dll'):
        p=Path(os.environ['SystemRoot'])/'System32'/name
        if p.is_file():
            identity['runtime_files'][str(p)]=sha(p)
    dump(out/'build/compiler.json',identity)
    return compiler,env,identity


def compile_binaries(out, config, compiler, env):
    build = out/'build'
    common = [PROJECT/'software/c/fft32.c', PROJECT/'software/c/mfcc.c', out/'coefficients/mfcc_tables.c']
    results = {}
    for label,main in (('mfcc_host',PROJECT/'software/c/host_main.c'),('test_core',PROJECT/'verification/c/test_core.c')):
        obj = build/label
        obj.mkdir()
        exe = build/(label+'.exe')
        command = [compiler,*config['flags'],'/I'+str(PROJECT/'software/c'),'/I'+str(out/'coefficients'),
                   '/Fo'+str(obj)+os.sep,'/Fe'+str(exe),*common,main]
        checked_process(command,build,build/(label+'_compile.log'),env)
        results[label] = {'path':str(exe),'sha256':sha(exe),'command_argv':[str(x) for x in command]}
    dump(build/'binaries.json',results)
    return results


def assert_reference(reference, index, relative):
    key = Path(relative).as_posix()
    path = reference/key
    if key not in index or not path.is_file() or sha(path) != index[key]:
        raise ValueError('Frozen Python artifact mismatch: '+key)
    return path


def reference_case(reference,index,role,clip):
    base=Path(role)/clip
    validation=read(assert_reference(reference,index,base/'validation.json'))
    if validation['passed'] is not True or validation['profiles']['comparison_raw13']['passed'] is not True:
        raise ValueError('Python reference case did not pass')
    pcm=assert_reference(reference,index,base/'input_s16le.pcm')
    if sha(pcm) != validation['pcm_sha256'] or pcm.stat().st_size != 2*validation['sample_count']:
        raise ValueError('PCM bytes differ from validated reference')
    folder=reference/base/'comparison_raw13'
    schema=read(assert_reference(reference,index,base/'comparison_raw13/arrays.json'))
    for info in schema['arrays'].values():
        p=assert_reference(reference,index,base/'comparison_raw13'/info['file'])
        if sha(p)!=info['sha256'] or p.stat().st_size!=info['bytes']:
            raise ValueError('Python array metadata mismatch')
    return pcm,folder,validation


def run_case(reference,index,out,binary,role,clip,tolerance):
    pcm,ref,old=reference_case(reference,index,role,clip)
    dest=out/role/clip
    dest.mkdir(parents=True)
    command=[binary,'--input',pcm,'--output',dest]
    checked_process(command,out,dest/'host_console.log')
    result=compare_case(ref,dest,tolerance)
    result.update(id=clip,role=role,input_sha256=sha(pcm),sample_count=old['sample_count'],
                  reference_directory=str(ref),command_argv=[str(x) for x in command])
    dump(dest/'validation.json',result)
    print(f'{role}: {clip}: {"PASS" if result["passed"] else "FAIL"} first={result.get("first_failed_stage")}',flush=True)
    return result


def summarize(items):
    aggregate={}
    for item in items:
        for stage,metric in item['stages'].items():
            if metric.get('max_abs') is None:
                continue
            record=aggregate.setdefault(stage,{'max_abs':0.0,'worst_clip':None,'worst_clip_rmse':0.0,
                'sum_squared_error':0.0,'elements':0,'violations':0,'passed':True})
            if metric['max_abs']>=record['max_abs']:
                record['max_abs'],record['worst_clip']=metric['max_abs'],item['id']
            record['worst_clip_rmse']=max(record['worst_clip_rmse'],metric['rmse'])
            record['sum_squared_error']+=metric['rmse']**2*metric['elements']
            record['elements']+=metric['elements']
            record['violations']+=metric['violations']
            record['passed']=bool(record['passed'] and metric['passed'])
    for record in aggregate.values():
        record['global_rmse']=(record['sum_squared_error']/record['elements'])**0.5 if record['elements'] else 0.0
    return {'inputs':len(items),'passed_count':sum(i['passed'] for i in items),
            'passed':all(i['passed'] for i in items),
            'structural_passed':all(i['structural']['passed'] for i in items),
            'frames':sum(i['structural']['expected_frame_count'] for i in items),
            'stages':aggregate,
            'failures':[{'id':i['id'],'first_failed_stage':i.get('first_failed_stage')} for i in items if not i['passed']]}


def export_metrics(out,items):
    with (out/'stage_errors.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['role','id','stage','max_abs','rmse','violations','passed'])
        writer.writeheader()
        for item in items:
            for stage,metric in item.get('stages',{}).items():
                writer.writerow({'role':item['role'],'id':item['id'],'stage':stage,
                                 **{k:metric.get(k) for k in ('max_abs','rmse','violations','passed')}})


def run(args,out):
    manifest={'started_at_kst':now(),'status':'running','phase':args.phase,'output':str(out),
              'command_argv':sys.orig_argv,'git_head':git(['rev-parse','HEAD']),
              'git_status':git(['status','--porcelain=v1']),'platform':sys.platform,
              'limitations':['PC numerical validation only; ARM hardware not executed.',
                             'No recognition, power or acceleration measurement.']}
    items=[]
    try:
        source=source_snapshot()
        config=read(PROJECT/'verification/c/build_config.json')
        tolerance=read(PROJECT/'verification/c/tolerances.json')
        limits=read(PROJECT/'verification/c/known_development_limits.json')
        index=read(args.reference_root/'artifact_manifest.json')
        reference_manifest=read(assert_reference(args.reference_root,index,'run_manifest.json'))
        if reference_manifest['status']!='passed':
            raise ValueError('Python reference run did not pass')
        dataset=read(assert_reference(args.reference_root,index,'dataset_manifest_snapshot.json'))
        dev=[e for e in dataset['clips'] if e['role']=='development']
        evaluation=[e for e in dataset['clips'] if e['role']=='evaluation']
        if len(dev)!=1 or len(evaluation)!=20:
            raise ValueError('Expected development1 and evaluation20')
        synthetic=sorted(p.name for p in (args.reference_root/'synthetic').iterdir() if p.is_dir())
        if len(synthetic)!=17:
            raise ValueError('Expected synthetic17')
        manifest.update(source_hashes=source,reference_root=str(args.reference_root),
            reference_artifact_manifest_sha256=sha(args.reference_root/'artifact_manifest.json'),
            python_reference_manifest_sha256=sha(args.reference_root/'run_manifest.json'),
            current_spec_sha256=sha(PROJECT/'docs/MFCC_SPEC.md'),
            reference_spec_sha256=reference_manifest['spec_sha256'],
            environment={'python':sys.version,'executable':sys.executable,
                         'packages':{n:importlib.metadata.version(n) for n in ('numpy','scipy','matplotlib')}})
        for relative in source:
            dst=out/'provenance/source_snapshot'/relative
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(PROJECT/relative,dst)
        shutil.copyfile(PROJECT/'docs/MFCC_SPEC.md',out/'provenance/MFCC_SPEC_current.md')
        shutil.copyfile(assert_reference(args.reference_root,index,'MFCC_SPEC_snapshot.md'),out/'provenance/MFCC_SPEC_python_snapshot.md')
        shutil.copyfile(args.reference_root/'dataset_manifest_snapshot.json',out/'provenance/dataset_manifest_snapshot.json')
        dump(out/'tolerances.json',tolerance)
        dump(out/'known_development_limits.json',limits)
        dump(out/'build_config.json',config)
        dump(out/'run_manifest.json',manifest)
        generate(args.reference_root,out/'coefficients')
        coefficient_hashes={p.relative_to(out/'coefficients').as_posix():sha(p)
                            for p in sorted((out/'coefficients').rglob('*'))
                            if p.is_file() and p.suffix in ('.bin','.h','.c')}
        compiler,env,identity=compiler_environment(out,config)
        binding={'source_hashes':source,'coefficient_hashes':coefficient_hashes,'compiler':identity,
                 'current_spec_sha256':manifest['current_spec_sha256'],
                 'tolerance':tolerance,'environment':manifest['environment'],
                 'reference_artifact_manifest_sha256':manifest['reference_artifact_manifest_sha256']}
        if args.phase=='evaluate':
            if args.development_run is None:
                raise ValueError('evaluate requires --development-run')
            freeze=read(args.development_run/'freeze.json')
            gate=read(args.development_run/'development_gate.json')
            old=read(args.development_run/'run_manifest.json')
            if (not gate['evaluation_eligible'] or old['status'] not in ('passed','completed_with_numerical_failures')
                or sha(args.development_run/'development_gate.json')!=freeze['development_gate_sha256']):
                raise ValueError('Development gate invalid')
            if sha(args.development_run/'diagnostics/diagnosis.json')!=gate['diagnosis_sha256']:
                raise ValueError('Required development precision diagnosis changed')
            if freeze['binding']!=binding:
                raise ValueError('Frozen code/settings/compiler/reference changed: repeat development')
            for name,record in freeze['binaries'].items():
                if sha(record['path'])!=record['sha256']:
                    raise ValueError('Frozen binary modified')
            binaries=freeze['binaries']
            shutil.copyfile(args.development_run/'freeze.json',out/'freeze.json')
        else:
            binaries=compile_binaries(out,config,compiler,env)
            test=checked_process([binaries['test_core']['path']],out,out/'core_contract_tests.log')
            manifest['core_contract_tests']=json.loads(test.stdout)
            if manifest['core_contract_tests'].get('passed') is not True:
                raise RuntimeError('Core contract tests failed')
            manifest['host_contract_tests']=run_host_checks(binaries['mfcc_host']['path'],out,args.reference_root)
            if not manifest['host_contract_tests']['passed']:
                raise RuntimeError('Host I/O or chunk/state contract tests failed')
            synthetic_results=[run_case(args.reference_root,index,out,binaries['mfcc_host']['path'],'synthetic',clip,tolerance) for clip in synthetic]
            development=run_case(args.reference_root,index,out,binaries['mfcc_host']['path'],'development',dev[0]['utterance_id'],tolerance)
            items=synthetic_results+[development]
            if development['structural']['expected_frame_count']!=534:
                raise RuntimeError('Development frame total is not 534')
            plot_case(args.reference_root/'development'/dev[0]['utterance_id']/'comparison_raw13',
                      out/'development'/dev[0]['utterance_id'],out/'figures',dev[0]['utterance_id'])
            diagnosis=diagnose(args.reference_root,out,out/'diagnostics')
            observed={i['id']:[s for s,m in i['stages'].items() if not m['passed']]
                      for i in synthetic_results if not i['passed']}
            documented=limits['allowed_numerical_failure_stages']
            understood=all(clip in documented and stages==documented[clip] for clip,stages in observed.items())
            eligible=(all(i['structural']['passed'] for i in items) and development['passed'] and understood)
            gate={'completed_at_kst':now(),'synthetic':summarize(synthetic_results),'development':summarize([development]),
                  'passed':all(i['passed'] for i in items),'evaluation_eligible':bool(eligible),
                  'observed_numerical_failures':observed,
                  'diagnosis_sha256':sha(out/'diagnostics/diagnosis.json'),
                  'note':'Numerical failures remain failures. Eligibility only allows a frozen evaluation study of the documented binary32 limits.',
                  'binding':binding}
            dump(out/'development_gate.json',gate)
            manifest['development_gate']=gate
            export_metrics(out,items)
            if not gate['evaluation_eligible']:
                raise RuntimeError('Unexpected development/structural failure; evaluation not opened')
            if source_snapshot()!=source or sha(PROJECT/'docs/MFCC_SPEC.md')!=binding['current_spec_sha256']:
                raise RuntimeError('Code changed before freeze')
            freeze={'frozen_at_kst':now(),'binding':binding,'binaries':binaries,
                    'development_gate_sha256':sha(out/'development_gate.json')}
            dump(out/'freeze.json',freeze)
        manifest['freeze_sha256']=sha(out/'freeze.json')
        manifest['development_numerical_acceptance_passed']=gate['passed']
        if args.phase in ('all','evaluate'):
            manifest['evaluation_started_at_kst']=now()
            evaluations=[]
            for entry in evaluation:
                if source_snapshot()!=source or sha(PROJECT/'docs/MFCC_SPEC.md')!=binding['current_spec_sha256']:
                    raise RuntimeError('Code changed during evaluation')
                evaluations.append(run_case(args.reference_root,index,out,binaries['mfcc_host']['path'],'evaluation',entry['utterance_id'],tolerance))
            items+=evaluations
            manifest['evaluation_summary']=summarize(evaluations)
            if manifest['evaluation_summary']['frames']!=9501:
                raise RuntimeError('Evaluation frame total is not 9501')
            if not manifest['evaluation_summary']['structural_passed']:
                raise RuntimeError('Evaluation structural failure; numerical errors cannot excuse malformed output')
        if (source_snapshot()!=source or sha(PROJECT/'docs/MFCC_SPEC.md')!=binding['current_spec_sha256']
            or sha(args.reference_root/'artifact_manifest.json')!=binding['reference_artifact_manifest_sha256']):
            raise RuntimeError('Source or Python reference index changed during execution')
        accepted=all(i['passed'] for i in items) and gate['passed']
        manifest['status']='passed' if accepted else 'completed_with_numerical_failures'
        manifest['numerical_acceptance_passed']=bool(accepted)
        manifest['input_hashes']={i['role']+'/'+i['id']:i['input_sha256'] for i in items}
    except Exception:
        manifest['status']='failed'
        manifest['error']=traceback.format_exc()
        raise
    finally:
        manifest['completed_at_kst']=now()
        export_metrics(out,items)
        dump(out/'run_manifest.json',manifest)
        dump(out/'artifact_manifest.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*'))
                                         if p.is_file() and p.name not in ('artifact_manifest.json','console.log')})
    print(json.dumps({'status':manifest['status'],'output':str(out),'evaluation':manifest.get('evaluation_summary')},ensure_ascii=False),flush=True)
    return 0 if manifest['status']=='passed' else 2


class Tee:
    def __init__(self,stream,log):
        self.stream,self.log=stream,log
    def write(self,text):
        self.stream.write(text)
        self.log.write(text)
    def flush(self):
        self.stream.flush()
        self.log.flush()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('all','develop','evaluate'),default='all')
    parser.add_argument('--run-id',default=datetime.now().strftime('%Y%m%dT%H%M%S%f'))
    parser.add_argument('--reference-root',type=Path,default=WORKSPACE/'build/python_reference/reproduce_01')
    parser.add_argument('--development-run',type=Path)
    args=parser.parse_args()
    if not args.run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.run_id):
        parser.error('Invalid run-id')
    args.reference_root=args.reference_root.resolve()
    out=BUILD/args.run_id
    out.mkdir(parents=True,exist_ok=False)
    stdout,stderr=sys.stdout,sys.stderr
    with (out/'console.log').open('w',encoding='utf-8') as log:
        sys.stdout,sys.stderr=Tee(stdout,log),Tee(stderr,log)
        try:
            return run(args,out)
        except Exception:
            traceback.print_exc()
            return 1
        finally:
            sys.stdout,sys.stderr=stdout,stderr
    return 0


if __name__=='__main__':
    raise SystemExit(main())
