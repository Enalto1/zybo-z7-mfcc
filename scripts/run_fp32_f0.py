"""Run real XPM xsim and OOC synthesis; retain all evidence outside the repository."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[1]
ROOT=PROJECT.parent/'build/fp32_hw'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,data):Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-id',required=True)
    p.add_argument('--vivado',default='C:/Xilinx/Vivado/2024.2/bin/vivado.bat')
    p.add_argument('--long-clip',action='store_true',help='One85920-word structural clip,534frames; no MFCC arithmetic')
    p.add_argument('--simulation-only',action='store_true',help='Skip unchanged-framer synthesis')
    args=p.parse_args()
    if not re.fullmatch(r'f0_[A-Za-z0-9_-]+',args.run_id):p.error('run-id must start f0_ and contain only letters/numbers/_/-')
    out=ROOT/args.run_id
    if out.exists():p.error('Existing evidence cannot be overwritten')
    out.mkdir(parents=True)
    files=['hardware/fp32/rtl/fp32_frame_buffer.sv','verification/fp32/f0/tb_fp32_frame_buffer.sv',
        'scripts/fp32_f0.tcl','scripts/run_fp32_f0.py','docs/RTL_CODING_RULES.md','docs/FP32_BUFFER_ARCHITECTURE.md']
    manifest=dict(started_at_utc=datetime.now(timezone.utc).isoformat(),status='running',
        part='xc7z020clg400-1',tool_target='Vivado 2024.2',
        source_sha256={name:sha(PROJECT/name) for name in files},board_executed=False,
        full_mfcc_numerics_validated=False,implementation_routed=False,
        long_clip=args.long_clip,synthesis_requested=not args.simulation_only,
        input_kind='deterministic32-bit sample identities; no audio read')
    try:
        rtl=(PROJECT/files[0]).read_text()
        stripped=re.sub(r'//[^\n]*|/\*.*?\*/','',rtl,flags=re.S)
        prohibited=re.findall(r'\b(?:for|function|task|initial|while|repeat|forever|casex)\b|\.\*',stripped)
        if prohibited:raise ValueError('Prohibited synthesized RTL tokens: '+str(prohibited))
        if len(re.findall(r'\balways_ff\b',stripped))!=1 or len(re.findall(r'\balways_comb\b',stripped))!=1:
            raise ValueError('Expected exactly one sequential and one combinational process')
        manifest['authored_style_static_check']='passed; vendor XPM/TB excluded'
        source=out/'source'
        for name in files:
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,target)
            if sha(target)!=manifest['source_sha256'][name]:raise ValueError('Source changed during snapshot: '+name)
        cmd=[args.vivado,'-mode','batch','-source',str(source/'scripts/fp32_f0.tcl'),'-notrace',
             '-log',str(out/'vivado.log'),'-journal',str(out/'vivado.jou'),'-tclargs',str(source),str(out),
             str(int(args.simulation_only)),str(int(args.long_clip))]
        manifest['command_argv']=cmd
        with (out/'console.log').open('w',encoding='utf-8') as log:
            result=subprocess.run(cmd,cwd=out,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError(f'Vivado exited {result.returncode}; inspect console.log')
        simlogs=list(out.rglob('simulate.log'))
        if len(simlogs)!=1:raise ValueError('Expected one xsim simulate.log')
        simtext=simlogs[0].read_text(errors='replace')
        passed=re.search(r'F0_LONG_PASS[^\r\n]*' if args.long_clip else r'F0_PASS[^\r\n]*',simtext)
        if not passed or re.search(r'Fatal:|FATAL|Error:',simtext):raise ValueError('XPM simulation did not pass')
        manifest['simulation_pass']=passed.group(0)
        if args.long_clip:
            coverage={key:int(value) for key,value in re.findall(r'(\w+)=(\d+)',passed.group(0))}
            required={'clips':1,'accepted_words':85920,'frames':534,'compared_words':273408,
                      'last_frame':533,'last_start':85280,'tail_discarded':128,'write_wraps':167,'read_wraps':534}
            if any(coverage.get(k)!=v for k,v in required.items()):raise ValueError('Long-clip exact coverage mismatch')
            manifest['long_clip_coverage']=coverage
        manifest['synthesis']={'status':'not_run_unchanged_rtl'} if args.simulation_only else json.loads((out/'synthesis_metrics.json').read_text())
        manifest['status']='passed_f0_only'
        print(passed.group(0));print(manifest['synthesis'])
        return 0
    except Exception as e:
        manifest['status']='failed';manifest['error']=str(e);print(str(e),file=sys.stderr);return 1
    finally:
        manifest['completed_at_utc']=datetime.now(timezone.utc).isoformat()
        dump(out/'run_manifest.json',manifest)
        dump(out/'artifact_manifest.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*'))
            if p.is_file() and p.name!='artifact_manifest.json' and '.Xil' not in p.parts})

if __name__=='__main__':raise SystemExit(main())
