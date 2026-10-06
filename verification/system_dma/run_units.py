"""Exclusive-slot, no-IP XSIM tests for the shared DMA transport boundary."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
BUILD = PROJECT.parent/'build/system_dma'
BIN = Path('C:/Xilinx/Vivado/2024.2/bin')
FILES = ['hardware/system_dma/mfcc_dma_transport.sv','hardware/system_dma/mfcc_dma_top.sv',
         'hardware/system_fp32/fp32_core_adapter.sv',
         'verification/system_dma/tb_mfcc_dma_transport.sv']

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value): path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]+',args.run_id): parser.error('Simple new run-id required')
    out=BUILD/args.run_id
    out.mkdir(parents=True,exist_ok=False)
    state=dict(status='RUNNING',started_utc=datetime.now(timezone.utc).isoformat(),board_access=False,
               scope='No arithmetic/DMA IP: packed input, record serialization, control and IRQ boundary',commands=[],sources={})
    def command(name, argv, timeout=300):
        started=time.monotonic()
        p=subprocess.run([str(x) for x in argv],cwd=out,text=True,capture_output=True,errors='replace',timeout=timeout)
        (out/(name+'.log')).write_text(p.stdout+'\n'+p.stderr,encoding='utf-8')
        state['commands'].append(dict(name=name,argv=[str(x) for x in argv],returncode=p.returncode,seconds=time.monotonic()-started))
        if p.returncode: raise RuntimeError(name+' failed; inspect saved log')
        return p.stdout+p.stderr
    try:
        process=command('process_inventory',['powershell','-NoProfile','-Command',
            'Get-Process vivado,xvlog,xelab,xsim -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path | ConvertTo-Json; exit 0'])
        if process.strip(): raise RuntimeError('Competing FPGA process found; exclusive slot required')
        for name in FILES:
            source=PROJECT/name;target=out/'source'/name
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            state['sources'][name]=sha(source)
        shutil.copyfile(__file__,out/'run_units_snapshot.py')
        style=[]
        for name in FILES[:3]:
            source=(out/'source'/name).read_text(encoding='utf-8')
            text=re.sub(r'/\*.*?\*/|//[^\n]*','',source,flags=re.S)
            forbidden=re.findall(r'\b(?:for|while|repeat|forever|function|task|initial)\b|\.\*',text)
            expected=0 if name.endswith('mfcc_dma_top.sv') else 1
            assert not forbidden and len(re.findall(r'\balways_ff\b',text))==expected and len(re.findall(r'\balways_comb\b',text))==expected
            style.append(dict(file=name,forbidden_tokens=[],always_ff=expected,always_comb=expected))
        state['static_style']=style
        state['tools']={tool:dict(path=str(BIN/(tool+'.bat')),sha256=sha(BIN/(tool+'.bat'))) for tool in ('xvlog','xelab','xsim')}
        command('xvlog',[BIN/'xvlog.bat','--sv',*[out/'source'/name for name in FILES]])
        for top,report in [('tb_mfcc_dma_transport','dma_transport_core1_unit_simulation.json'),
                           ('tb_mfcc_dma_transport_fp32','dma_transport_core2_unit_simulation.json')]:
            command(top+'_xelab',[BIN/'xelab.bat',top,'-s',top+'_snapshot','-debug','off','--timescale','1ns/1ps','--mt','2'])
            # Each simulation cwd is its unique run directory; benches default
            # RUN to '.', avoiding Windows batch splitting of KEY=value args.
            log=command(top+'_xsim',[BIN/'xsim.bat',top+'_snapshot','-runall'])
            expected='DMA_TRANSPORT_UNIT_PASS'
            if expected not in log: raise RuntimeError(top+' missing pass sentinel')
            result=json.loads((out/report).read_text())
            if result['status']!='PASS': raise RuntimeError(top+' failed report')
            state[top]=result
        state['status']='PASS'
        return 0
    except Exception as error:
        state.update(status='FAIL',error=str(error));print(error,file=sys.stderr);return 1
    finally:
        state['completed_utc']=datetime.now(timezone.utc).isoformat()
        save(out/'unit_manifest.json',state)
        save(out/'artifact_hashes.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='artifact_hashes.json'})

if __name__=='__main__': raise SystemExit(main())
