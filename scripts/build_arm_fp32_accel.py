"""Build/test the new MMIO transport; optionally build a fresh BSP from a pinned system XSA.

Never connects to a board, downloads an ELF/bitstream, creates BOOT.bin or writes flash.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT.parent/'build'
VITIS=Path('C:/Xilinx/Vitis/2024.2')
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'verification/arm_fp32_accel'))
import references
CPU=['-mcpu=cortex-a9','-mfpu=vfpv3','-mfloat-abi=hard']
ARM_FLAGS=CPU+['-std=c11','-O2','-g3','-fno-tree-vectorize','-fno-lto',
               '-Wall','-Wextra','-Werror','-Wconversion','-Wsign-conversion','-fstack-usage']

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def run(argv, out: Path, log_name: str, *, env=None, stdin=None) -> str:
    command=list(map(str,argv))
    clean=os.environ.copy() if env is None else env.copy()
    for key in ('CFLAGS','CPPFLAGS','LDFLAGS','GCC_EXEC_PREFIX','COMPILER_PATH',
                'LIBRARY_PATH','CPATH','C_INCLUDE_PATH','CL','_CL_','LINK'):
        clean.pop(key,None)
    result=subprocess.run(command,cwd=out,env=clean,input=stdin,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          encoding='utf-8',errors='replace',timeout=1200)
    (out/'logs'/log_name).write_text(json.dumps(command)+'\n'+result.stdout,encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'{log_name}: command returned {result.returncode}')
    return result.stdout

def snapshot(out: Path) -> dict:
    paths=sorted((ROOT/'software/arm_fp32_accel').rglob('*'))
    paths += sorted((ROOT/'verification/arm_fp32_accel').rglob('*'))
    paths += [ROOT/'docs/MFCC_SPEC.md',ROOT/'docs/FP32_HARDWARE_RESULTS.md',Path(__file__)]
    hashes={}
    for path in paths:
        if not path.is_file() or path.suffix not in ('.c','.h','.tcl','.md','.py'): continue
        relative=path.relative_to(ROOT)
        destination=out/'source'/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        digest=sha(path); shutil.copy2(path,destination)
        if sha(destination)!=digest or sha(path)!=digest: raise ValueError(f'Snapshot changed: {path}')
        hashes[relative.as_posix()]=digest
    return hashes

def generate_smoke(out: Path, gold: Path) -> dict:
    bundle=references.load(gold)
    prefix=bundle['pcm'][:1024]
    values=bundle['bits'][:13]
    expected=struct.pack('<13Q',*values)
    pcm=struct.unpack('<512h',prefix)
    generated=out/'source/generated'; generated.mkdir(parents=True)
    header=['#ifndef ACCEL_SMOKE_VECTORS_H','#define ACCEL_SMOKE_VECTORS_H','#include <stdint.h>',
            f'#define ACCEL_SMOKE_PCM_CRC32 UINT32_C(0x{zlib.crc32(prefix):08x})',
            '#define ACCEL_SMOKE_BFP_S (0)','static const int16_t accel_smoke_pcm[512]={']
    header += [','.join(str(v) for v in pcm[i:i+16])+',' for i in range(0,512,16)]
    header += ['};','static const uint64_t accel_smoke_expected[13]={']
    header += [f'UINT64_C(0x{v:08x}),' for v in values]
    header += ['};','#endif','']
    (generated/'accel_smoke_vectors.h').write_text('\n'.join(header),encoding='ascii')
    (generated/'pcm512.bin').write_bytes(prefix); (generated/'mfcc13_bits64.bin').write_bytes(expected)
    dump(out/'reference_identity.json',bundle['metadata'])
    return {'reference':bundle['metadata'],'source_samples':85920,
            'prefix_samples':512,'expected_records':13,'bfp_s':0,
            'pcm_sha256':hashlib.sha256(prefix).hexdigest(),'mfcc_sha256':hashlib.sha256(expected).hexdigest(),
            'header_sha256':sha(generated/'accel_smoke_vectors.h')}

def host_tests(out: Path, vcvars: Path) -> dict:
    capture=out/'capture_environment.cmd'
    capture.write_text('@echo off\ncall "'+str(vcvars)+'" >nul\nif errorlevel 1 exit /b 1\nset\n',encoding='utf-8')
    response=subprocess.run(['cmd.exe','/d','/c',str(capture)],capture_output=True,text=True,errors='replace',timeout=60)
    if response.returncode: raise RuntimeError('MSVC environment initialization failed')
    env=os.environ.copy()
    for line in response.stdout.splitlines():
        if '=' in line and not line.startswith('='):
            key,value=line.split('=',1); env[key]=value
    # The desktop host may inherit both PATH and Path. vcvars modifies PATH;
    # keep its configured spelling instead of the stale duplicate from the host.
    configured_path=next((line[5:] for line in response.stdout.splitlines() if line.upper().startswith('PATH=')),None)
    if configured_path is None: raise RuntimeError('vcvars did not publish PATH')
    for key in list(env):
        if key.upper()=='PATH': env.pop(key)
    env['PATH']=configured_path
    compiler=shutil.which('cl.exe',path=configured_path)
    if not compiler: raise RuntimeError('MSVC compiler missing')
    flags=['/nologo','/TC','/std:c11','/O2','/W4','/WX','/MD']
    source=out/'source'; objects=[]
    for name in ('software/arm_fp32_accel/mfcc_fp32_accel.c','verification/arm_fp32_accel/test_driver.c'):
        obj=out/'objects'/(Path(name).stem+'.obj'); objects.append(obj)
        run([compiler,*flags,'/I'+str(source/'software/arm_fp32_accel'),'/c',source/name,'/Fo'+str(obj)],
            out,Path(name).stem+'_host_compile.log',env=env)
    executable=out/'binaries/test_driver.exe'
    run([compiler,'/nologo',*objects,'/Fe'+str(executable)],out,'host_link.log',env=env)
    result=run([executable],out,'host_tests.log',env=env)
    match=re.search(r'PASS ARM_FP32_ACCEL_HOST checks=(\d+)',result)
    if not match: raise ValueError('Host test PASS marker missing')
    return {'status':'PASS','checks':int(match.group(1)),'compiler':str(compiler),
            'compiler_sha256':sha(Path(compiler)),'vcvars_sha256':sha(vcvars),
            'flags':flags,'executable_sha256':sha(executable)}

def make_linker(template: Path, destination: Path) -> None:
    value=template.read_text(encoding='utf-8')
    value,n=re.subn(r'(ps7_ddr_0\s*:\s*ORIGIN\s*=\s*)0x[0-9a-fA-F]+(\s*,\s*LENGTH\s*=\s*)0x[0-9a-fA-F]+',
                    r'\g<1>0x00100000\g<2>0x02000000',value)
    if n!=1: raise ValueError('Unexpected generated linker DDR region')
    value,n=re.subn(r'(_STACK_SIZE\s*=\s*DEFINED\(_STACK_SIZE\)\s*\?\s*_STACK_SIZE\s*:\s*)0x[0-9a-fA-F]+',
                    r'\g<1>0x10000',value)
    if n!=1: raise ValueError('Unexpected generated linker stack declaration')
    value+='\nASSERT(_stack <= ORIGIN(ps7_ddr_0)+LENGTH(ps7_ddr_0), "Accelerator app exceeds reserved DDR")\n'
    destination.write_text(value,encoding='utf-8')

def arm_syntax(out: Path, include: Path) -> dict:
    """Read an existing BSP only for compilation; never link a platform ELF."""
    if not (include/'xparameters.h').is_file(): raise ValueError('Syntax-only BSP include missing')
    before={p.relative_to(include).as_posix():sha(p) for p in sorted(include.rglob('*')) if p.is_file()}
    gcc=VITIS/'gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe'
    version=run([gcc,'--version'],out,'arm_compiler_version.log')
    source=out/'source'
    includes=['-I'+str(p) for p in (source/'software/arm_fp32_accel',source/'generated',include)]
    objects={}
    for name in ('mfcc_fp32_accel','mfcc_fp32_accel_xilinx','demo_main'):
        obj=out/'objects'/(name+'_syntax.o')
        run([gcc,*ARM_FLAGS,*includes,'-c',source/'software/arm_fp32_accel'/(name+'.c'),'-o',obj],
            out,name+'_arm_syntax.log')
        objects[name]=sha(obj)
    after={p.relative_to(include).as_posix():sha(p) for p in sorted(include.rglob('*')) if p.is_file()}
    if before!=after: raise ValueError('Read-only syntax BSP changed during compilation')
    dump(out/'syntax_bsp_header_hashes.json',before)
    return {'status':'OBJECTS_ONLY_NOT_SYSTEM_ELF','bsp_include':str(include.resolve()),
            'compiler':str(gcc),'compiler_sha256':sha(gcc),'compiler_version':version,
            'flags':ARM_FLAGS,'objects':objects,'bsp_files_verified_unchanged':len(before)}

def arm_build(out: Path, xsa: Path, xsa_sha: str) -> dict:
    if sha(xsa)!=xsa_sha: raise ValueError('System XSA does not match supplied SHA-256')
    with zipfile.ZipFile(xsa) as archive:
        names=archive.namelist()
        bits=[n for n in names if n.lower().endswith('.bit')]
        hwh=[n for n in names if n.lower().endswith('.hwh')]
        if len(bits)!=1 or not hwh: raise ValueError('Require PL system XSA containing bitstream and HWH')
        hw='\n'.join(archive.read(n).decode('utf-8',errors='strict') for n in hwh).lower()
        if '0x43c00000' not in hw or '0x43c0ffff' not in hw:
            raise ValueError('XSA lacks required accelerator 0x43C00000/64KiB address range')
        init=[n for n in names if n.endswith('ps7_init.tcl')]
        if len(init)!=1: raise ValueError('Expected one system-specific PS initialization script')
        (out/'ps7_init.tcl').write_bytes(archive.read(init[0]))
        bit_hash=hashlib.sha256(archive.read(bits[0])).hexdigest()
    inputs=out/'inputs'; inputs.mkdir()
    pinned=inputs/'mfcc_fp32_system.xsa'; shutil.copy2(xsa,pinned)
    if sha(pinned)!=xsa_sha: raise ValueError('XSA changed while snapshotting')
    source=out/'source'; workspace=out/'workspace'
    bsp_log=run([VITIS/'bin/xsct.bat',source/'software/arm_fp32_accel/create_bsp.tcl',pinned,workspace],out,'bsp.log')
    if 'FP32_ACCEL_BSP_AND_TEMPLATE_COMPLETE' not in bsp_log.splitlines():
        raise ValueError('XSCT BSP completion sentinel missing')
    candidates=[p.parent for p in workspace.rglob('xparameters.h')
                if 'export' not in p.parts and (p.parent.parent/'lib/libxil.a').is_file()]
    if len(candidates)!=1: raise ValueError(f'Ambiguous new system BSP: {candidates}')
    include=candidates[0]; lib=include.parent/'lib'; xp=(include/'xparameters.h').read_text()
    if not re.search(r'XPAR_PS7_DDR_0_S_AXI_HIGHADDR\s+0x3fffffff',xp,re.I):
        raise ValueError('New system BSP DDR range differs from board preset')
    gcc_bin=VITIS/'gnu/aarch32/nt/gcc-arm-none-eabi/bin'; gcc=gcc_bin/'arm-none-eabi-gcc.exe'
    version=run([gcc,'--version'],out,'arm_compiler_version.log')
    specs=out/'Xilinx.spec'; shutil.copy2(workspace/'accel_template/src/Xilinx.spec',specs)
    linker=out/'accel_linker.ld'; make_linker(workspace/'accel_template/src/lscript.ld',linker)
    includes=['-I'+str(p) for p in (source/'software/arm_fp32_accel',source/'generated',include)]
    objects=[]
    for name in ('mfcc_fp32_accel','mfcc_fp32_accel_xilinx','demo_main'):
        obj=out/'objects'/(name+'.o'); objects.append(obj)
        run([gcc,*ARM_FLAGS,*includes,'-c',source/'software/arm_fp32_accel'/(name+'.c'),'-o',obj],out,name+'_arm_compile.log')
    elf=out/'binaries/mfcc_fp32_accel_demo.elf'
    run([gcc,*CPU,'-specs='+str(specs),'-Wl,-T,'+str(linker),'-Wl,-Map,'+str(out/'binaries/mfcc_fp32_accel_demo.map'),
         '-Wl,--build-id=none','-o',elf,*objects,'-L'+str(lib),'-Wl,--start-group','-lxil','-lc','-lgcc','-Wl,--end-group'],
        out,'arm_link.log')
    symbols=run([gcc_bin/'arm-none-eabi-nm.exe','-n','-S',elf],out,'arm_symbols.log')
    captured={}
    for address,size,name in re.findall(r'^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+[A-Za-z]\s+(arm_fp32_accel_\w+)$',symbols,re.M):
        captured[name]={'address':int(address,16),'bytes':int(size,16)}
    required={'arm_fp32_accel_pcm':524288,'arm_fp32_accel_results':21268*24,'arm_fp32_accel_control':32,'arm_fp32_accel_layout':64}
    for name,size in required.items():
        item=captured.get(name,{})
        if item.get('bytes')!=size or not 0x100000<=item.get('address',0)<0x2100000-size:
            raise ValueError(f'ELF DDR bounds/layout failure: {name} {item}')
    for name in ('arm_fp32_accel_status','arm_fp32_accel_ready_breakpoint','arm_fp32_accel_result_breakpoint'):
        if name not in captured: raise ValueError(f'ELF symbol missing: {name}')
    for tool,args in (('readelf',['-h','-A','-l','-S']),('objdump',['-d']),('size',['-A'])):
        run([gcc_bin/f'arm-none-eabi-{tool}.exe',*args,elf],out,'arm_'+tool+'.log')
    return {'status':'BUILT_NOT_BOARD_RUN','xsa':str(xsa.resolve()),'xsa_sha256':xsa_sha,
            'bitstream_sha256':bit_hash,'ps7_init_sha256':sha(out/'ps7_init.tcl'),
            'compiler':str(gcc),'compiler_sha256':sha(gcc),'compiler_version':version,'flags':ARM_FLAGS,
            'bsp_include':str(include),'libxil_sha256':sha(lib/'libxil.a'),
            'xparameters_sha256':sha(include/'xparameters.h'),'specs_sha256':sha(specs),
            'linker_sha256':sha(linker),'elf':str(elf),'elf_sha256':sha(elf),'symbols':captured}

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--host-tests',action='store_true')
    parser.add_argument('--syntax-bsp',type=Path,help='Read-only BSP include path for objects only, never a final ELF')
    parser.add_argument('--xsa',type=Path)
    parser.add_argument('--xsa-sha256')
    parser.add_argument('--system-run',help='Select a completed build/system_fp32 run and its pinned XSA')
    parser.add_argument('--gold',type=Path,default=references.GOLD)
    parser.add_argument('--vcvars',type=Path,default=Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat'))
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.run_id): parser.error('Use a simple new run ID')
    system_manifest=None
    if args.system_run:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',args.system_run): parser.error('Invalid system run name')
        if args.xsa or args.xsa_sha256: parser.error('Use --system-run or the explicit --xsa/--xsa-sha256 pair')
        system_path=BASE/'system_fp32'/args.system_run
        system_manifest=read_json(system_path/'run_manifest.json')
        if system_manifest['status']!='complete': parser.error('FP32 system build is not complete')
        if system_manifest['source_sha256']['hardware/fp32/rtl/fp32_backend.sv']!=references.BACKEND_SHA:
            parser.error('FP32 system backend does not match frozen gold')
        args.xsa=system_path/'design/zybo_z7_20_fp32.xsa'
        args.xsa_sha256=system_manifest['outputs']['xsa']['sha256']
    if not args.host_tests and args.xsa is None and args.syntax_bsp is None:
        parser.error('Choose --host-tests, --syntax-bsp and/or --xsa')
    if (args.xsa is None)!=(args.xsa_sha256 is None): parser.error('--xsa and --xsa-sha256 must be paired')
    if args.xsa_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}',args.xsa_sha256): parser.error('Expected lowercase SHA-256')
    out=BASE/'arm_fp32_accel'/args.run_id; out.mkdir(parents=True,exist_ok=False)
    for name in ('logs','objects','binaries'): (out/name).mkdir()
    manifest={'started_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'status':'building',
              'board_run':False,'hardware_numeric_comparison':False,'board_performance_measured':False}
    if system_manifest is not None:
        manifest['system_run']=args.system_run
        manifest['system_manifest_sha256']=sha(system_path/'run_manifest.json')
    print(out,flush=True)
    try:
        manifest['sources']=snapshot(out)
        manifest['smoke']=generate_smoke(out,args.gold)
        if args.host_tests: manifest['host_tests']=host_tests(out,args.vcvars)
        if args.syntax_bsp is not None: manifest['arm_syntax']=arm_syntax(out,args.syntax_bsp)
        if args.xsa is not None: manifest['arm']=arm_build(out,args.xsa,args.xsa_sha256)
        manifest['status']='complete'
    except Exception as exc:
        manifest['status']='failed'; manifest['error']=str(exc); print(str(exc),file=sys.stderr)
    manifest['completed_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
    dump(out/'build_manifest.json',manifest)
    exclusions={'.metadata','.Xil','IDE.log','.analytics'}
    dump(out/'artifact_hashes.json',{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*'))
         if p.is_file() and p.name!='artifact_hashes.json' and not any(part in exclusions for part in p.relative_to(out).parts)})
    return 0 if manifest['status']=='complete' else 1

if __name__=='__main__': raise SystemExit(main())
