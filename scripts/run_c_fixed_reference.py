"""Freeze, build and validate the partial integer C FFT -> Power -> Mel port.

Every new run is exclusive. --verify continues only an existing prepared run.
The source snapshot for PC is copied to <run>/source_snapshot; no live integer
Python model is imported. Evaluation speech is neither opened nor selected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import traceback

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
sys.path.insert(0, str(PROJECT / "verification/c_fixed"))
import numpy as np
from frozen_reference import prepare, sha, read, dump


def checked(command, folder, log, env=None, success=True):
    command = [str(x) for x in command]
    result = subprocess.run(command, cwd=folder, env=env, capture_output=True,
                            text=True, errors="replace")
    log.write_text("ARGV " + json.dumps(command) + "\n" + result.stdout + "\n" + result.stderr +
                   "\nEXIT " + str(result.returncode) + "\n", encoding="utf-8")
    if success and result.returncode:
        raise RuntimeError("Command failed, see " + str(log))
    return result


def msvc_environment(folder):
    vcvars = Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat")
    capture = folder / "capture_environment.cmd"
    capture.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\nset\n',encoding="ascii")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(capture)], capture_output=True,
                            text=True, errors="replace")
    if result.returncode: raise RuntimeError("MSVC initialization failed: " + result.stderr)
    env = os.environ.copy()
    for line in result.stdout.splitlines():
        if "=" in line and not line.startswith("="):
            key, value = line.split("=",1)
            env[key] = value
    for key in ("CL", "_CL_", "LINK"):
        env.pop(key,None)
    compiler = shutil.which("cl.exe",path=env.get("Path",env.get("PATH")))
    if not compiler: raise RuntimeError("cl.exe unavailable after vcvars64")
    return compiler, env


def snapshot_sources(out):
    folder = out / "source_snapshot"
    folder.mkdir()
    source_hashes = {}
    for relative in ("software/c_fixed", "verification/c_fixed"):
        for path in sorted((PROJECT/relative).rglob("*")):
            if path.is_file() and path.suffix in (".c", ".h", ".py", ".json", ".md"):
                rel = path.relative_to(PROJECT)
                target = folder / rel
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target)
                source_hashes[rel.as_posix()] = sha(target)
    runner = Path(__file__)
    target = folder / "scripts" / runner.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(runner,target)
    source_hashes["scripts/"+runner.name] = sha(target)
    dump(out / "source_hashes.json", source_hashes)
    return folder, source_hashes


def compare_output(path, expected, label):
    actual = np.fromfile(path,dtype="<i8")
    if actual.size != expected.size:
        raise ValueError(f"{label}: wrong output count {actual.size}, expected {expected.size}")
    actual = actual.reshape(expected.shape)
    bounds = {"metadata": (0,6), "fft_re_all512": (6,518), "fft_im_all512": (518,1030),
              "power_257": (1030,1287), "mel_26": (1287,1313),
              "promotion": (1313,2337), "group_L512": (2337,3361),
              "group_L128": (3361,4385), "group_L32": (4385,5409),
              "group_L8": (5409,6433), "trailing_L2": (6433,7457),
              "overflow_after_group": (7457,7462)}
    stages = {}
    for key,(start,end) in bounds.items():
        different = actual[:,start:end] != expected[:,start:end]
        stages[key] = {"values": int(different.size), "mismatches": int(np.count_nonzero(different))}
        if np.any(different):
            first = np.argwhere(different)[0]
            stages[key]["first_mismatch"] = {"record":int(first[0]), "index":int(first[1]),
                "expected":int(expected[first[0],start+first[1]]),"actual":int(actual[first[0],start+first[1]])}
    return actual,{"passed":all(v["mismatches"]==0 for v in stages.values()),
                   "stages":stages,"output_sha256":sha(path)}


def malformed_tests(exe, out, build, env):
    good = (out/"contract/input.bin").read_bytes()
    frame_bytes = 4 + 512*2*2
    one = good[:8] + struct.pack("<I",1) + good[12:12+frame_bytes]
    zero = good[:8] + struct.pack("<I",0)
    malformed = {"bad_magic":b"BAD!"+good[4:12], "short_header":good[:9],
                 "short_exponent":one[:14], "short_real":one[:100],
                 "short_imag":one[:-1], "trailing":one+b"X",
                 "invalid_exp_low":one[:12]+struct.pack("<i",-3)+one[16:],
                 "invalid_exp_high":one[:12]+struct.pack("<i",25)+one[16:],
                 "too_many_frames":good[:8]+struct.pack("<I",1000001)}
    tests=[]
    for name,contents in [("zero_frames",zero),*malformed.items()]:
        inp = build/(name+".bin"); inp.write_bytes(contents)
        output = build/(name+"_output.bin")
        process=checked([exe,inp,output],build,build/(name+".log"),env,success=False)
        success = process.returncode==0 if name=="zero_frames" else process.returncode!=0
        if name=="zero_frames": success = success and output.stat().st_size==0
        tests.append({"name":name,"exit_code":process.returncode,"passed":success})
    prior=build/"zero_frames_output.bin"
    prior_hash=sha(prior)
    repeat=checked([exe,build/"zero_frames.bin",prior],build,build/"exclusive_output.log",env,success=False)
    tests.append({"name":"refuse_existing_output","exit_code":repeat.returncode,
                  "passed":repeat.returncode!=0 and sha(prior)==prior_hash})
    if not all(t["passed"] for t in tests): raise ValueError("Malformed/zero frame test failed")
    return tests


def numerical_errors(actual, out):
    cases = read(out/"contract/cases.json")
    dct = np.load(out/"contract/frozen_float64_coefficients.npz",allow_pickle=False)["dct_matrix"]
    reports=[]; aggregate={}
    for case in cases:
        if case["group"] in ("integer_adversarial","published_directed"): continue
        start,nf=case["first_record"],case["frames"]
        rows=actual[start:start+nf]
        ref=np.load(out/"contract"/case["reference_file"],allow_pickle=False)
        s=rows[:,0]
        fft=(rows[:,6:263]+1j*rows[:,518:775])*np.exp2((-9-s)[:,None])
        power=rows[:,1030:1287].astype(float)*np.exp2(rows[:,1,None])
        mel=rows[:,1287:1313].astype(float)*np.exp2(rows[:,2,None])
        log=np.log(np.maximum(mel,1e-12)); mfcc=log@dct.T
        result={"id":case["id"],"group":case["group"],"frames":nf,"stages":{},
                "floor_regressions":int(np.sum((ref["mel_energies"]>1e-12)&(mel<1e-12))),
                "mel_cells_below_floor":int(np.sum(mel<1e-12))}
        coefficient_diff=np.abs(mfcc-ref["mfcc"])
        result["raw13_host_per_coefficient"]=[{"coefficient":i,
             "max_abs":float(np.max(coefficient_diff[:,i])) if nf else 0.0,
             "rmse":float(np.sqrt(np.mean(coefficient_diff[:,i]**2))) if nf else 0.0}
             for i in range(13)]
        group="development" if case["group"]=="development" else "synthetic"
        agg=aggregate.setdefault(group,{"frames":0,"floor_regressions":0,"stages":{}})
        agg["frames"]+=nf; agg["floor_regressions"]+=result["floor_regressions"]
        for name,values in (("fft",fft),("power",power),("mel_energies",mel),("log_mel",log),("mfcc",mfcc)):
            if values.shape!=ref[name].shape: raise ValueError("Numerical reference shape mismatch")
            diff=np.abs(values-ref[name]); sse=float(np.sum(diff**2)); count=diff.size
            metric={"max_abs":float(np.max(diff)) if count else 0.0,
                    "rmse":float(np.sqrt(sse/count)) if count else 0.0,"elements":int(count),"sse":sse}
            result["stages"][name]=metric
            a=agg["stages"].setdefault(name,{"max_abs":0.0,"sse":0.0,"elements":0})
            a["max_abs"]=max(a["max_abs"],metric["max_abs"]); a["sse"]+=sse; a["elements"]+=count
        reports.append(result)
    for agg in aggregate.values():
        for metric in agg["stages"].values():
            metric["rmse"]=(metric["sse"]/metric["elements"])**0.5 if metric["elements"] else 0.0
    return {"scope":"Host float64 reconstruction/log/DCT diagnostics from C integer FFT/Power/Mel; NOT full fixed MFCC",
            "algorithm_accuracy_pass":None,"acceptance_tolerance":"not published",
            "aggregate":aggregate,"cases":reports}


def verify(out):
    contract=read(out/"contract/contract.json")
    prepared=read(out/"prepare.json")
    if sha(out/"contract/contract.json") != prepared["contract_sha256"]:
        raise ValueError("Contract changed after preparation")
    header=(out/"contract/c_fixed_contract.h").read_text(encoding="ascii")
    if '#define C_FIXED_CONTRACT_VERSION "'+contract["version"]+'"' not in header or \
       '#define C_FIXED_CONTRACT_SHA256 "'+prepared["contract_sha256"]+'"' not in header:
        raise ValueError("Compiled contract identity header mismatch")
    for name,digest in contract["generated_sha256"].items():
        if sha(out/"contract"/name)!=digest: raise ValueError("Prepared artifact changed: "+name)
    source,source_hashes=snapshot_sources(out)
    folder=out/"pc"; folder.mkdir()
    compiler,env=msvc_environment(folder)
    clang=Path(r"C:\Xilinx\Vitis\2024.2\vcxx\libexec\clang.exe")
    builds=[("msvc_O2",compiler,["/nologo","/TC","/std:c11","/O2","/W4","/WX","/MD"],env),
            ("msvc_RTC",compiler,["/nologo","/TC","/std:c11","/Od","/RTC1","/W4","/WX","/MD"],env)]
    if clang.is_file():
        builds.append(("clang_ubsan_O2",str(clang),["--sysroot=C:/Xilinx/Vitis/2024.2/vcxx","-fuse-ld=lld",
                    "-std=c11","-O2","-Wall","-Wextra","-Werror","-Wconversion","-Wshadow",
                    "-fsanitize=undefined","-fno-sanitize-recover=all"],None))
    expected=np.fromfile(out/"contract/expected_i64le.bin",dtype="<i8").reshape(contract["records"],contract["values_per_record"])
    summaries=[]; first_actual=None
    for name,cc,flags,cenv in builds:
        build=folder/name; build.mkdir()
        is_msvc=name.startswith("msvc")
        includes=[source/"software/c_fixed",out/"contract"]
        common=[source/"software/c_fixed/fixed_int.c",source/"software/c_fixed/mfcc_fixed.c",out/"contract/c_fixed_tables.c"]
        identity=checked([cc],build,build/"compiler_version.log",cenv,False) if is_msvc else checked([cc,"--version"],build,build/"compiler_version.log",cenv)
        executables={}
        for label,main in (("host",source/"verification/c_fixed/host_main.c"),("test_integer",source/"verification/c_fixed/test_integer.c")):
            exe=build/(label+".exe")
            defines=(["/DCF_TEST_CORE"] if is_msvc else ["-DCF_TEST_CORE"]) if label=="test_integer" else []
            if is_msvc:
                obj=build/label; obj.mkdir()
                command=[cc,*flags,*defines,*["/I"+str(i) for i in includes],"/Fo"+str(obj)+os.sep,"/Fe"+str(exe),*common,main]
            else:
                command=[cc,*flags,*defines,*["-I"+str(i) for i in includes],*common,main,"-o",exe]
            checked(command,build,build/(label+"_compile.log"),cenv)
            executables[label]={"path":str(exe),"sha256":sha(exe),"command":[str(x) for x in command]}
        checked([executables["test_integer"]["path"]],build,build/"test_integer_run.log",cenv)
        output=build/"actual_i64le.bin"
        run=checked([executables["host"]["path"],out/"contract/input.bin",output],build,build/"host_run.log",cenv)
        actual,comparison=compare_output(output,expected,name)
        malformed=malformed_tests(executables["host"]["path"],out,build,cenv)
        summary={"build":name,"compiler":str(cc),"compiler_sha256":sha(cc),"flags":flags,
                 "version":identity.stdout+identity.stderr,"executables":executables,
                 "comparison":comparison,"malformed_tests":malformed,"host_report":json.loads(run.stdout.strip())}
        dump(build/"result.json",summary)
        summaries.append(summary)
        if first_actual is None:first_actual=actual
        print(name+": "+("PASS" if comparison["passed"] else "FAIL"),flush=True)
    numbers=numerical_errors(first_actual,out)
    dump(out/"numerical_errors.json",numbers)
    directed=contract.get("published_directed_frames",0)
    result={"status":"passed" if all(s["comparison"]["passed"] for s in summaries) else "failed",
            "scope":"partial FFT-Power-Mel C port", "contract_version":contract["version"],
            "contract_sha256":sha(out/"contract/contract.json"),"source_hashes":source_hashes,
            "published_contract_sha256":contract.get("published_contract_sha256"),
            "records":contract["records"],"original_frames":contract["original_frames"],
            "published_directed_frames":directed,
            "adversarial_frames":contract["adversarial_frames"],
            "fft_overflow_frames":int(np.sum(first_actual[:,3])),
            "original_fft_overflow_frames":int(np.sum(first_actual[:contract["original_frames"],3])),
            "published_directed_fft_overflow_frames":int(np.sum(first_actual[contract["original_frames"]:contract["original_frames"]+directed,3])),
            "adversarial_fft_overflow_frames":int(np.sum(first_actual[contract["original_frames"]+directed:,3])),
            "builds":summaries,"algorithm_accuracy_pass":None,"ARM_executed":False}
    dump(out/"pc_results.json",result)
    arm_files=list((out/"source_snapshot/software/c_fixed").glob("*"))
    arm_files += [p for p in (out/"contract").iterdir() if p.suffix in (".c",".h")]
    arm_files += [out/"prepare.json",out/"pc_results.json",out/"contract/contract.json",out/"source_hashes.json"]
    dump(out/"arm_input_hashes.json",{p.relative_to(out).as_posix():sha(p) for p in arm_files if p.is_file()})
    dump(out/"pc_artifact_hashes.json",{str(p.relative_to(out)).replace("\\","/"):sha(p)
         for p in sorted(out.rglob("*")) if p.is_file() and "arm" not in p.relative_to(out).parts
         and p.name!="pc_artifact_hashes.json"})
    if result["status"]!="passed":raise ValueError("C bit comparison failed; preserved mismatch reports")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--frozen",type=Path,default=Path(r"D:\2610_MFCC\build\fft_precision\prec_04_verified_full_20261004"))
    parser.add_argument("--prepare-only",action="store_true")
    parser.add_argument("--verify",action="store_true")
    parser.add_argument("--published-contract",type=Path,
                        help="Explicit PUBLISHED fixed_contract package. Otherwise choose latest completed publication once.")
    args=parser.parse_args()
    if not args.run_id.startswith("c_fixed") or Path(args.run_id).name!=args.run_id:
        parser.error("run-id must be one safe folder name starting c_fixed")
    if args.prepare_only and args.verify:parser.error("choose prepare-only or verify")
    out=PROJECT.parent/"build"/args.run_id
    if not args.verify:out.mkdir(exist_ok=False)
    elif not (out/"prepare.json").is_file():parser.error("--verify requires a prepared run")
    try:
        if not args.verify:
            published=args.published_contract
            if published is None:
                candidates=list((PROJECT.parent/"build/fixed_contract").glob("*/PUBLISHED.json"))
                complete=[(read(p)["published_at"],p.parent) for p in candidates if read(p).get("status")=="PUBLISHED"]
                if complete:published=max(complete,key=lambda x:x[0])[1]
            prepare(args.frozen,out,PROJECT,published)
        if not args.prepare_only:verify(out)
    except Exception:
        path=out/("verify_failure.txt" if args.verify else "failure.txt")
        path.write_text(traceback.format_exc(),encoding="utf-8")
        raise
    print(out,flush=True)


if __name__=="__main__":main()
