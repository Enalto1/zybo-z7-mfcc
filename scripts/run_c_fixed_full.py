"""Pin published v2, build portable full integer C, compare every stage and state.

Runs are exclusive and contain all compiler commands, snapshots and hashes.
No board operation and no evaluation speech access. v1 runners are unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import traceback

sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[1]
for key in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"):os.environ[key]="1"
sys.path.insert(0,str(PROJECT/"verification/c_fixed"))
import numpy as np
from full_reference import DEFAULT_PACKAGE,prepare,sha,read,dump,compare_actual,numerical


def checked(command,cwd,log,env=None,required=True):
    argv=[str(x) for x in command]
    result=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,errors="replace")
    log.write_text("ARGV "+json.dumps(argv)+"\n"+result.stdout+"\n"+result.stderr+"\nEXIT "+str(result.returncode)+"\n",encoding="utf-8")
    if required and result.returncode:raise RuntimeError("Command failed, see "+str(log))
    return result


def msvc_environment(out):
    vcvars=Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat")
    batch=out/"capture_environment.cmd"
    batch.write_text('@echo off\ncall "'+str(vcvars)+'" >nul\nif errorlevel 1 exit /b 1\nset\n',encoding="ascii")
    process=subprocess.run(["cmd.exe","/d","/c",str(batch)],capture_output=True,text=True,errors="replace")
    if process.returncode:raise RuntimeError("MSVC environment failed")
    env=os.environ.copy();captured_paths=[]
    for line in process.stdout.splitlines():
        if "=" in line and not line.startswith("="):
            key,value=line.split("=",1);env[key]=value
            if key.casefold()=="path":captured_paths.append(value)
    for key in ("CL","_CL_","LINK"):env.pop(key,None)
    compiler=None;compiler_path=None
    for candidate in reversed(captured_paths):
        located=shutil.which("cl.exe",path=candidate)
        if located:compiler,compiler_path=located,candidate;break
    if not compiler:raise RuntimeError("MSVC compiler missing in captured PATH values")
    for key in list(env):
        if key.casefold()=="path":env.pop(key)
    env["PATH"]=compiler_path
    return compiler,env


def snapshot(out):
    folder=out/"source_snapshot";folder.mkdir()
    paths=list((PROJECT/"software/c_fixed").glob("*"))
    paths += [p for p in (PROJECT/"verification/c_fixed").glob("*") if p.name.startswith(("full_","v2_","test_full"))]
    paths += [Path(__file__)]
    hashes={}
    for path in sorted(paths):
        if not path.is_file() or path.suffix not in (".c",".h",".py",".json",".md"):continue
        relative=path.relative_to(PROJECT);target=folder/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        hashes[relative.as_posix()]=sha(target)
    dump(out/"source_hashes.json",hashes)
    return folder,hashes


def malformed(exe,out,build,env):
    folder=build/"invalid_input";folder.mkdir()
    good=(out/"contract/input.bin").read_bytes()
    values={"zero_cases":b"CFUL0002"+struct.pack("<I",0),
            "short_header":good[:9],"bad_magic":b"bad!!!!!"+good[8:12],
            "too_many_cases":good[:8]+struct.pack("<I",1001),
            "short_count":good[:14],"short_pcm":good[:100],
            "odd_pcm":good[:101],"trailing_byte":b"CFUL0002"+struct.pack("<I",0)+b"X",
            "too_many_samples":b"CFUL0002"+struct.pack("<II",1,10000001)}
    tests=[]
    for name,blob in values.items():
        inp=folder/(name+".bin");inp.write_bytes(blob)
        outputs=[folder/(name+suffix) for suffix in (".pre.bin",".frames.bin",".states.bin")]
        result=checked([exe,inp,*outputs,"0"],folder,folder/(name+".log"),env,False)
        passed=result.returncode==0 if name=="zero_cases" else result.returncode!=0
        if name=="zero_cases":passed=passed and all(p.stat().st_size==0 for p in outputs)
        tests.append({"name":name,"exit_code":result.returncode,"passed":passed})
    paths=[folder/("zero_cases"+s) for s in (".pre.bin",".frames.bin",".states.bin")]
    hashes=[sha(p) for p in paths]
    repeat=checked([exe,folder/"zero_cases.bin",*paths,"0"],folder,folder/"exclusive.log",env,False)
    tests.append({"name":"existing_output_preserved","exit_code":repeat.returncode,
                  "passed":repeat.returncode!=0 and hashes==[sha(p) for p in paths]})
    if not all(t["passed"] for t in tests):raise ValueError("Host invalid input guard failure")
    return tests


def verify(out):
    contract=read(out/"contract/contract.json");prepared=read(out/"prepare.json")
    if sha(out/"contract/contract.json")!=prepared["contract_sha256"]:raise ValueError("Prepared contract changed")
    header=(out/"contract/c_fixed_contract.h").read_text(encoding="ascii")
    if '#define C_FIXED_CONTRACT_SHA256 "'+prepared["contract_sha256"]+'"' not in header:raise ValueError("Compiled contract hash mismatch")
    for name,digest in contract["generated_sha256"].items():
        if sha(out/"contract"/name)!=digest:raise ValueError("Prepared file changed: "+name)
    source,source_hashes=snapshot(out);pc=out/"pc";pc.mkdir()
    cc,env=msvc_environment(pc)
    clang=Path(r"C:\Xilinx\Vitis\2024.2\vcxx\libexec\clang.exe")
    settings=[("msvc_O2",cc,["/nologo","/TC","/std:c11","/O2","/W4","/WX","/MD"],env),
              ("msvc_RTC",cc,["/nologo","/TC","/std:c11","/Od","/RTC1","/W4","/WX","/MD"],env),
              ("clang_ubsan_O2",clang,["--sysroot=C:/Xilinx/Vitis/2024.2/vcxx","-fuse-ld=lld","-std=c11","-O2",
                 "-Wall","-Wextra","-Werror","-Wconversion","-Wshadow","-fsanitize=undefined","-fno-sanitize-recover=all"],None)]
    expected_state=np.fromfile(out/"contract/expected_states_i64le.bin",dtype="<i8").reshape(contract["cases"],521)
    summaries=[];reference_actual=reference_pre=None
    for label,compiler,flags,cenv in settings:
        build=pc/label;build.mkdir();msvc=label.startswith("msvc")
        version=checked([compiler] if msvc else [compiler,"--version"],build,build/"compiler_version.log",cenv,not msvc)
        includes=[source/"software/c_fixed",out/"contract"]
        common=[source/"software/c_fixed"/f for f in ("fixed_int.c","mfcc_fixed.c","mfcc_fixed_full.c")]
        common += [out/"contract/c_fixed_tables.c",out/"contract/c_fixed_full_tables.c"]
        executables={}
        for name,main in (("full_host",source/"verification/c_fixed/full_host_main.c"),
                          ("test_full_core",source/"verification/c_fixed/test_full_core.c")):
            exe=build/(name+".exe")
            if msvc:
                objects=build/name;objects.mkdir()
                command=[compiler,*flags,*["/I"+str(p) for p in includes],"/Fo"+str(objects)+os.sep,"/Fe"+str(exe),*common,main]
            else:command=[compiler,*flags,*["-I"+str(p) for p in includes],*common,main,"-o",exe]
            checked(command,build,build/(name+"_compile.log"),cenv)
            executables[name]={"path":str(exe),"sha256":sha(exe),"command":[str(x) for x in command]}
        unit=checked([executables["test_full_core"]["path"]],build,build/"test_full_core_run.log",cenv)
        modes=[]
        for mode,name in enumerate(("bulk4096","single_sample","ragged_chunks","reset_after173","reset_after600")):
            folder=build/name;folder.mkdir()
            paths=[folder/(suffix+".bin") for suffix in ("pre","frames","states")]
            process=checked([executables["full_host"]["path"],out/"contract/input.bin",*paths,str(mode)],folder,folder/"host_run.log",cenv)
            actual,pre,comparison=compare_actual(paths[0],paths[1],out)
            states=np.fromfile(paths[2],dtype="<i8")
            if states.size!=expected_state.size:raise ValueError("State record count mismatch")
            differences=states.reshape(expected_state.shape)!=expected_state
            state_comparison={"values":int(differences.size),"mismatches":int(np.count_nonzero(differences))}
            if np.any(differences):
                case,col=np.argwhere(differences)[0]
                state_comparison["first"]={"case":int(case),"column":int(col),"expected":int(expected_state[case,col]),"actual":int(states.reshape(expected_state.shape)[case,col])}
            comparison["states"]=state_comparison;comparison["passed"] &= state_comparison["mismatches"]==0
            item={"mode":name,"comparison":comparison,"host_report":json.loads(process.stdout.strip())}
            dump(folder/"comparison.json",item);modes.append(item)
            if reference_actual is None:reference_actual,reference_pre=actual,pre
        invalid=malformed(executables["full_host"]["path"],out,build,cenv)
        summary={"build":label,"compiler":str(compiler),"compiler_sha256":sha(compiler),"version":version.stdout+version.stderr,
                 "flags":flags,"executables":executables,"unit_test_stdout":unit.stdout,"modes":modes,"malformed_tests":invalid,
                 "passed":all(m["comparison"]["passed"] for m in modes)}
        dump(build/"result.json",summary);summaries.append(summary)
        print(label+": "+("PASS" if summary["passed"] else "FAIL"),flush=True)
    metrics=numerical(reference_actual,reference_pre,out);dump(out/"numerical_errors.json",metrics)
    result={"status":"passed" if all(s["passed"] for s in summaries) else "failed",
            "scope":"full integer PCM16 to raw13 signed40/F24","contract_version":contract["version"],
            "contract_sha256":sha(out/"contract/contract.json"),"published_contract_sha256":contract["published_contract_sha256"],
            "source_hashes":source_hashes,"cases":contract["cases"],"frames":contract["frames"],"pcm_samples":contract["pcm_samples"],
            "algorithm_accuracy_pass":None,"algorithm_accuracy":"NOT_ACCEPTED","ARM_executed":False,"builds":summaries,
            "normal_fft_overflow_frames":int(np.sum(reference_actual[:,6])),"input_clips":int(np.sum(reference_actual[:,9])),
            "bfp_clamped_frames":int(np.sum(reference_actual[:,10]))}
    # Additional published helper boundaries use the same immutable C snapshot.
    # This supplements the complete PCM corpus without changing its gold values.
    from full_published_boundaries import run_boundary_audit
    boundary_report=run_boundary_audit(out)
    result["published_boundaries"]={"report":"published_boundaries_01/report.json",
                                     "status":boundary_report["status"]}
    dump(out/"pc_results.json",result)
    inputs=list((out/"source_snapshot/software/c_fixed").glob("*"))
    inputs += [p for p in (out/"contract").iterdir() if p.suffix in (".c",".h")]
    inputs += [out/"prepare.json",out/"pc_results.json",out/"source_hashes.json",out/"contract/contract.json"]
    dump(out/"arm_input_hashes.json",{p.relative_to(out).as_posix():sha(p) for p in inputs if p.is_file()})
    dump(out/"pc_artifact_hashes.json",{p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob("*")) if p.is_file() and p.name!="pc_artifact_hashes.json"})
    if result["status"]!="passed":raise ValueError("Integer bit comparison failed; reports preserved")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--published-contract",type=Path,default=DEFAULT_PACKAGE)
    parser.add_argument("--prepare-only",action="store_true")
    parser.add_argument("--verify",action="store_true")
    args=parser.parse_args()
    if not args.run_id.startswith("c_fixed") or Path(args.run_id).name!=args.run_id:parser.error("Safe c_fixed run-id required")
    if args.prepare_only and args.verify:parser.error("Choose prepare-only or verify")
    out=PROJECT.parent/"build"/args.run_id
    if not args.verify:out.mkdir(exist_ok=False)
    elif not (out/"prepare.json").is_file():parser.error("Prepared run required")
    try:
        if not args.verify:prepare(args.published_contract,out)
        if not args.prepare_only:verify(out)
    except Exception:
        failure=out/("verify_failure.txt" if args.verify else "failure.txt")
        with failure.open("x",encoding="utf-8") as handle:handle.write(traceback.format_exc())
        raise
    print(out,flush=True)


if __name__=="__main__":main()
