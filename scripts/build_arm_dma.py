"""Offline-only common DMA/IRQ firmware builder. Never connects to a board."""
from __future__ import annotations
import argparse
import datetime as dt
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT.parent/'build'
from build_arm_fp32_accel import sha,dump,read_json,run,make_linker,CPU,ARM_FLAGS,VITIS
sys.path.insert(0,str(ROOT/'verification/arm_dma'))
import dma_references as references

def snapshot(out):
    paths=list((ROOT/'software/arm_dma').rglob('*'))+list((ROOT/'verification/arm_dma').rglob('*'))
    paths += [Path(__file__),ROOT/'scripts/build_arm_fp32_accel.py',ROOT/'scripts/run_board_dma.py',
              ROOT/'verification/arm_fp32_accel/references.py',ROOT/'hardware/system_dma/REGISTER_CONTRACT.md',ROOT/'docs/MFCC_SPEC.md']
    hashes={}
    for path in sorted(paths):
        if not path.is_file() or path.suffix not in ('.c','.h','.tcl','.md','.py'):continue
        name=path.relative_to(ROOT);dest=out/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        digest=sha(path);shutil.copy2(path,dest)
        if sha(dest)!=digest or sha(path)!=digest:raise ValueError('Source changed during snapshot: '+str(path))
        hashes[name.as_posix()]=digest
    return hashes

def vectors(out,variant):
    import zlib
    bundle=references.load(variant);gen=out/'source/generated';gen.mkdir(parents=True)
    pcm=struct.unpack('<512h',bundle['pcm'][:1024])
    header=['#ifndef DMA_VECTORS_H','#define DMA_VECTORS_H','#include "mfcc_dma.h"',
        f'#define DMA_SMOKE_CRC UINT32_C(0x{zlib.crc32(bundle["pcm"][:1024]):08x})','static const int16_t dma_smoke_pcm[512]={']
    header += [','.join(map(str,pcm[i:i+16]))+',' for i in range(0,512,16)]
    header += ['};','static const mfcc_dma_record dma_expected[6942]={']
    header += ['{UINT64_C(0x%016x),%uU,%uU,%d,%uU},'%row for row in struct.iter_unpack('<QIIiI',bundle['records'])]
    header += ['};','#endif','']
    (gen/'dma_vectors.h').write_text('\n'.join(header),encoding='ascii')
    (gen/'development_pcm.bin').write_bytes(bundle['pcm']);(gen/'expected_records.bin').write_bytes(bundle['records'])
    dump(out/'reference_identity.json',bundle['metadata'])
    return dict(reference=bundle['metadata'],header_sha256=sha(gen/'dma_vectors.h'),
                expected_records_sha256=sha(gen/'expected_records.bin'),pcm_sha256=sha(gen/'development_pcm.bin'))

def host(out,vcvars):
    capture=out/'capture_environment.cmd'
    capture.write_text('@echo off\ncall "'+str(vcvars)+'" >nul\nif errorlevel 1 exit /b 1\nset\n',encoding='utf-8')
    response=subprocess.run(['cmd.exe','/d','/c',str(capture)],capture_output=True,text=True,errors='replace',timeout=60)
    if response.returncode:raise RuntimeError('MSVC environment failed')
    env=os.environ.copy()
    for line in response.stdout.splitlines():
        if '=' in line and not line.startswith('='):
            k,v=line.split('=',1);env[k]=v
    configured=next(line[5:] for line in response.stdout.splitlines() if line.upper().startswith('PATH='))
    for k in list(env):
        if k.upper()=='PATH':env.pop(k)
    env['PATH']=configured;compiler=shutil.which('cl.exe',path=configured)
    if not compiler:raise RuntimeError('Missing compiler')
    flags=['/nologo','/TC','/std:c11','/O2','/W4','/WX','/MD'];objects=[]
    for name in ('software/arm_dma/mfcc_dma.c','verification/arm_dma/test_driver.c'):
        obj=out/'objects'/(Path(name).stem+'.obj');objects.append(obj)
        run([compiler,*flags,'/I'+str(out/'source/software/arm_dma'),'/c',out/'source'/name,'/Fo'+str(obj)],out,Path(name).stem+'_host.log',env=env)
    executable=out/'binaries/test_driver.exe'
    run([compiler,'/nologo',*objects,'/Fe'+str(executable)],out,'host_link.log',env=env)
    text=run([executable],out,'host_tests.log',env=env);match=re.search(r'PASS ARM_DMA_HOST checks=(\d+)',text)
    if not match:raise ValueError('Host test PASS sentinel missing')
    return dict(status='PASS',checks=int(match[1]),compiler=str(compiler),compiler_sha256=sha(Path(compiler)),flags=flags,executable_sha256=sha(executable))

def platform_header(out,include,syntax_only=False):
    xp=(include/'xparameters.h').read_text();macros=dict(re.findall(r'^#define\s+(\w+)\s+(\S+)',xp,re.M))
    def resolve(name):
        value=macros[name].strip('()')
        return resolve(value) if value in macros else int(re.sub(r'[uUlL]+$','',value),0)
    def choose(names,expected=None):
        found=[n for n in names if n in macros]
        if not found:raise ValueError('Missing generated macro: '+str(names))
        values={resolve(n) for n in found}
        if len(values)!=1 or expected is not None and values!={expected}:raise ValueError('Unexpected macro values: '+str(found))
        return dict(macro=found[0],value=resolve(found[0]))
    fields={
      'DMA_GIC_DEVICE_ID':choose(['XPAR_SCUGIC_SINGLE_DEVICE_ID','XPAR_SCUGIC_0_DEVICE_ID']),
      'DMA_TIMER_DEVICE_ID':choose(['XPAR_SCUTIMER_DEVICE_ID','XPAR_SCUTIMER_0_DEVICE_ID','XPAR_XSCUTIMER_0_DEVICE_ID','XPAR_PS7_SCUTIMER_0_DEVICE_ID']),
      'DMA_TIMER_IRQ':dict(macro='ARM Cortex-A9 private timer PPI',value=29)}
    if syntax_only:
        fields.update(DMA_DEVICE_ID=dict(value=0,macro='UNVERIFIED_SYNTAX_PLACEHOLDER'),
                      **{k:dict(value=v,macro='UNVERIFIED_SYNTAX_PLACEHOLDER') for k,v in [('DMA_TX_IRQ',61),('DMA_RX_IRQ',62),('DMA_CORE_IRQ',63)]})
    else:
        fields['DMA_DEVICE_ID']=choose(['XPAR_AXIDMA_0_DEVICE_ID','XPAR_AXI_DMA_0_DEVICE_ID'])
        fields['DMA_TX_IRQ']=choose(['XPAR_FABRIC_AXI_DMA_0_MM2S_INTROUT_INTR'],61)
        fields['DMA_RX_IRQ']=choose(['XPAR_FABRIC_AXI_DMA_0_S2MM_INTROUT_INTR'],62)
        fields['DMA_CORE_IRQ']=choose(['XPAR_FABRIC_MFCC_DMA_0_IRQ_INTR','XPAR_FABRIC_MFCC_DMA_0_INTERRUPT_INTR'],63)
        choose(['XPAR_AXIDMA_0_BASEADDR','XPAR_AXI_DMA_0_BASEADDR'],0x40400000)
    dest=out/'source/generated/dma_platform.h'
    dest.write_text('#ifndef DMA_PLATFORM_H\n#define DMA_PLATFORM_H\n'+''.join(f'#define {k} {v["value"]}U\n' for k,v in fields.items())+'#endif\n',encoding='ascii')
    return dict(syntax_only_unverified=syntax_only,fields=fields,header_sha256=sha(dest))

def compile_objects(out,include,variant,syntax=False):
    platform=platform_header(out,include,syntax)
    gcc=VITIS/'gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe'
    extra=[]
    if syntax:
        drivers=VITIS/'data/embeddedsw/XilinxProcessorIPLib/drivers'
        extra=[drivers/'axidma_v9_18/src',drivers/'scugic_v5_3/src',drivers/'scutimer_v2_6/src']
    flags=ARM_FLAGS+['-DMFCC_DMA_CORE_ID='+str(1 if variant=='fixed' else 2)]
    includes=['-I'+str(p) for p in [out/'source/software/arm_dma',out/'source/generated',include,*extra]]
    objects=[]
    for name in ('mfcc_dma','mfcc_dma_xilinx','demo_main'):
        obj=out/'objects'/(name+'.o');objects.append(obj)
        run([gcc,*flags,*includes,'-c',out/'source/software/arm_dma'/(name+'.c'),'-o',obj],out,name+'_arm.log')
    return objects,dict(compiler=str(gcc),compiler_sha256=sha(gcc),flags=flags,platform=platform)

def xsa_configuration(xsa):
    with zipfile.ZipFile(xsa) as archive:
        hw=[n for n in archive.namelist() if n.endswith('.hwh')]
        if len(hw)!=1:raise ValueError('Expected one HWH')
        tree=ET.fromstring(archive.read(hw[0]))
        dma=[m for m in tree.iter('MODULE') if m.get('INSTANCE')=='axi_dma_0']
        if len(dma)!=1:raise ValueError('Expected axi_dma_0')
        params={p.get('NAME'):p.get('VALUE') for p in dma[0].iter('PARAMETER')}
        expected={'C_INCLUDE_SG':0,'C_INCLUDE_MM2S':1,'C_INCLUDE_S2MM':1,'C_INCLUDE_MM2S_DRE':1,'C_INCLUDE_S2MM_DRE':1,
                  'C_M_AXI_MM2S_DATA_WIDTH':64,'C_M_AXI_S2MM_DATA_WIDTH':64,'C_M_AXIS_MM2S_TDATA_WIDTH':32,
                  'C_S_AXIS_S2MM_TDATA_WIDTH':32,'C_SG_LENGTH_WIDTH':23,'C_MM2S_BURST_SIZE':16,'C_S2MM_BURST_SIZE':16}
        for name,wanted in expected.items():
            if name not in params or int(params[name],0)!=wanted:raise ValueError('DMA HWH parameter mismatch: '+name)
        bits=[n for n in archive.namelist() if n.endswith('.bit')]
        if len(bits)!=1:raise ValueError('Expected one embedded bitstream')
        import hashlib
        return dict(parameters={k:params[k] for k in expected},bitstream_sha256=hashlib.sha256(archive.read(bits[0])).hexdigest(),
                    ps7_init=archive.read('ps7_init.tcl'))

def arm_build(out,xsa,xsa_sha,variant):
    if sha(xsa)!=xsa_sha:raise ValueError('XSA hash mismatch')
    configuration=xsa_configuration(xsa);(out/'ps7_init.tcl').write_bytes(configuration.pop('ps7_init'))
    inputs=out/'inputs';inputs.mkdir();pinned=inputs/'system_dma.xsa';shutil.copy2(xsa,pinned)
    if sha(pinned)!=xsa_sha:raise ValueError('XSA snapshot changed')
    workspace=out/'workspace'
    text=run([VITIS/'bin/xsct.bat',out/'source/software/arm_dma/create_bsp.tcl',pinned,workspace],out,'bsp.log')
    if 'DMA_BSP_AND_TEMPLATE_COMPLETE' not in text.splitlines():raise ValueError('BSP completion sentinel missing')
    includes=[p.parent for p in workspace.rglob('xparameters.h') if 'export' not in p.parts and (p.parent.parent/'lib/libxil.a').is_file()]
    if len(includes)!=1:raise ValueError('Ambiguous system BSP')
    include=includes[0];lib=include.parent/'lib'
    objects,meta=compile_objects(out,include,variant)
    gcc=Path(meta['compiler']);bin=gcc.parent
    specs=out/'Xilinx.spec';shutil.copy2(workspace/'dma_template/src/Xilinx.spec',specs)
    linker=out/'dma_linker.ld';make_linker(workspace/'dma_template/src/lscript.ld',linker)
    elf=out/'binaries/mfcc_dma_demo.elf'
    run([gcc,*CPU,'-specs='+str(specs),'-Wl,-T,'+str(linker),'-Wl,-Map,'+str(out/'binaries/mfcc_dma_demo.map'),
         '-Wl,--build-id=none','-o',elf,*objects,'-L'+str(lib),'-Wl,--start-group','-lxil','-lc','-lgcc','-Wl,--end-group'],out,'arm_link.log')
    symbols=run([bin/'arm-none-eabi-nm.exe','-n','-S',elf],out,'arm_symbols.log');captured={}
    for address,size,name in re.findall(r'^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+[A-Za-z]\s+(arm_dma_\w+)$',symbols,re.M):
        captured[name]=dict(address=int(address,16),bytes=int(size,16))
    sizes={'pcm':524288,'results':510432,'history':33*166608,'trials':33*168,'control':48,'status':216,'layout':128}
    for name,size in sizes.items():
        item=captured.get('arm_dma_'+name,{})
        if item.get('bytes')!=size or not 0x100000<=item.get('address',0)<=0x2100000-size or item['address']%64:
            raise ValueError('ELF buffer bounds/alignment: '+name)
    for name in ('arm_dma_ready_breakpoint','arm_dma_result_breakpoint'):
        if name not in captured:raise ValueError('Missing ELF breakpoint: '+name)
    for tool,args in [('readelf',['-h','-A','-l','-S']),('objdump',['-d']),('size',['-A'])]:
        run([bin/f'arm-none-eabi-{tool}.exe',*args,elf],out,'arm_'+tool+'.log')
    return dict(status='BUILT_NOT_BOARD_RUN',xsa=str(xsa.resolve()),xsa_sha256=xsa_sha,
        bitstream_sha256=configuration['bitstream_sha256'],hwh_dma=configuration['parameters'],
        ps7_init_sha256=sha(out/'ps7_init.tcl'),bsp_include=str(include),xparameters_sha256=sha(include/'xparameters.h'),
        libxil_sha256=sha(lib/'libxil.a'),specs_sha256=sha(specs),linker_sha256=sha(linker),
        elf=str(elf),elf_sha256=sha(elf),symbols=captured,**meta)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variant',choices=['fixed','fp32'],required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--host-tests',action='store_true');p.add_argument('--syntax-bsp',type=Path)
    p.add_argument('--system-run');p.add_argument('--vcvars',type=Path,default=Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat'))
    a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',a.run_id):p.error('New simple run ID required')
    if not any([a.host_tests,a.syntax_bsp,a.system_run]):p.error('Select offline tests, syntax compilation or system build')
    system=None
    if a.system_run:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',a.system_run):p.error('Invalid system run ID')
        system_path=BASE/'system_dma'/a.system_run;system=read_json(system_path/'run_manifest.json')
        if system['status']!='complete' or system['variant']!=a.variant:p.error('Completed matching DMA system required')
    out=BASE/'arm_dma'/a.run_id;out.mkdir(parents=True,exist_ok=False)
    for name in ('logs','objects','binaries'):(out/name).mkdir()
    m=dict(status='building',variant=a.variant,core_id=1 if a.variant=='fixed' else 2,
           started_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),board_run=False,board_performance_measured=False)
    print(out,flush=True)
    try:
        m['sources']=snapshot(out);m['vectors']=vectors(out,a.variant)
        if a.host_tests:m['host_tests']=host(out,a.vcvars)
        if a.syntax_bsp:
            before={str(v.relative_to(a.syntax_bsp)):sha(v) for v in a.syntax_bsp.rglob('*') if v.is_file()}
            objects,meta=compile_objects(out,a.syntax_bsp,a.variant,True)
            after={str(v.relative_to(a.syntax_bsp)):sha(v) for v in a.syntax_bsp.rglob('*') if v.is_file()}
            if before!=after:raise ValueError('Read-only syntax BSP changed')
            m['syntax']=dict(status='OBJECTS_ONLY_UNVERIFIED_PLATFORM_NOT_SYSTEM_ELF',objects={v.name:sha(v) for v in objects},bsp_files_unchanged=len(before),**meta)
        if system:
            m['system_run']=a.system_run;m['system_manifest_sha256']=sha(system_path/'run_manifest.json')
            xsa=system_path/'design'/f'mfcc_dma_{a.variant}.xsa'
            m['arm']=arm_build(out,xsa,system['outputs']['xsa']['sha256'],a.variant)
        m['status']='complete'
    except Exception as e:m['status']='failed';m['error']=str(e);print(str(e),file=sys.stderr)
    m['completed_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat();dump(out/'build_manifest.json',m)
    excluded={'.metadata','.Xil','IDE.log','.analytics'}
    dump(out/'artifact_hashes.json',{v.relative_to(out).as_posix():sha(v) for v in sorted(out.rglob('*'))
         if v.is_file() and v.name!='artifact_hashes.json' and not any(k in excluded for k in v.relative_to(out).parts)})
    return 0 if m['status']=='complete' else 1
if __name__=='__main__':raise SystemExit(main())
