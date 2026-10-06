"""Immutable v2 contract audit, exact coefficient export and host-only metrics."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import sys

import numpy as np

VERSION = "v2_pcm16_mfcc40_20261004_r2"
DEFAULT_PACKAGE = Path(r"D:\2610_MFCC\build\fixed_contract") / VERSION
REFERENCE = Path(r"D:\2610_MFCC\build\fft_precision\prec_04_verified_full_20261004")
FRAME_COLUMNS = 2407
LAYOUT = {"metadata": (0, 11), "windowed_q30": (11, 523), "fft_input": (523, 1035),
          "fft_re": (1035, 1547), "fft_im": (1547, 2059), "power_u40": (2059, 2316),
          "mel_u60": (2316, 2342), "log_q24": (2342, 2368), "floor": (2368, 2394),
          "mfcc_q24": (2394, 2407)}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""): h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def c_array(values, suffix="", width=12):
    codes = [str(int(v))+suffix for v in np.asarray(values).ravel()]
    return "{\n"+",\n".join("    "+", ".join(codes[i:i+width]) for i in range(0,len(codes),width))+"\n}"


def descriptor(root, value):
    path = (root/value["file"]).resolve()
    if not path.is_relative_to(root.resolve()): raise ValueError("Descriptor escapes package")
    if sha(path)!=value["sha256"]: raise ValueError("Descriptor hash mismatch: "+str(path))
    if "bytes" in value and path.stat().st_size!=value["bytes"]: raise ValueError("Descriptor byte count mismatch")
    if "dtype" in value:
        result = np.fromfile(path,dtype=value["dtype"])
        if result.size != int(np.prod(value["shape"])): raise ValueError("Descriptor array size mismatch")
        return result.reshape(value["shape"])
    return path


def audit(package):
    pub = read(package/"PUBLISHED.json")
    if pub["status"]!="PUBLISHED" or pub["version"]!=VERSION: raise ValueError("Unsupported or incomplete publication")
    for name,key in (("contract.json","contract_sha256"),("artifact_hashes.json","artifact_manifest_sha256"),
                     ("verification.json","verification_sha256")):
        if sha(package/name)!=pub[key]: raise ValueError("Publication digest mismatch: "+name)
    hashes=read(package/"artifact_hashes.json")
    for relative,digest in hashes.items():
        path=(package/relative).resolve()
        if not path.is_relative_to(package.resolve()) or sha(path)!=digest:
            raise ValueError("Artifact mismatch: "+relative)
    contract=read(package/"contract.json")
    verification=read(package/"verification.json")
    if contract["version"]!=VERSION or verification["status"]!="PASS": raise ValueError("Contract verification is not PASS")
    count=0
    def visit(node):
        nonlocal count
        if isinstance(node,dict):
            if "file" in node and "sha256" in node:
                descriptor(package,node); count+=1
            for value in node.values(): visit(value)
        elif isinstance(node,list):
            for value in node: visit(value)
    visit(contract)
    return contract,{"passed":True,"publication":pub,"artifact_hashes_checked":len(hashes),
                     "descriptors_checked":count,"publisher_verification_record":verification,
                     "source_hashes":{k:v for k,v in hashes.items() if k.startswith("model_snapshot/")}}


def import_model(snapshot):
    name="c_fixed_v2_published"
    directory=snapshot/"software/fixed_model"
    spec=importlib.util.spec_from_file_location(name,directory/"__init__.py",submodule_search_locations=[str(directory)])
    module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module)
    full=importlib.import_module(name+".full_integer")
    fft=importlib.import_module(name+".fft_bitmodel")
    for key,value in sys.modules.items():
        if key.startswith(name) and not Path(value.__file__).resolve().is_relative_to(snapshot.resolve()):
            raise ValueError("Non-snapshot model import")
    return full,fft


def export_tables(folder, coefficient, twiddle):
    (folder/"c_fixed_tables.h").write_text('#ifndef C_FIXED_TABLES_H\n#define C_FIXED_TABLES_H\n#include "mfcc_fixed.h"\nextern const cf_tables c_fixed_tables;\n#endif\n',encoding="ascii")
    (folder/"c_fixed_tables.c").write_text('#include "c_fixed_tables.h"\nconst cf_tables c_fixed_tables = {\n'+c_array(twiddle[0])+',\n'+c_array(twiddle[1])+',\n{\n'+',\n'.join(c_array(row,"U") for row in coefficient["mel_q16"])+ '\n}\n};\n',encoding="ascii")
    (folder/"c_fixed_full_tables.h").write_text('#ifndef C_FIXED_FULL_TABLES_H\n#define C_FIXED_FULL_TABLES_H\n#include "mfcc_fixed_full.h"\nextern const cf_full_tables c_fixed_full_tables;\n#endif\n',encoding="ascii")
    (folder/"c_fixed_full_tables.c").write_text('#include "c_fixed_full_tables.h"\nconst cf_full_tables c_fixed_full_tables = {\n'+c_array(coefficient["window_q30"],"U")+',\n{\n'+',\n'.join(c_array(row) for row in coefficient["dct_q30"])+ '\n}\n};\n',encoding="ascii")
    np.asarray(twiddle,dtype="<i2").tofile(folder/"twiddle_re_im_s16le.bin")
    for name in ("window_q30","mel_q16","dct_q30"):
        coefficient[name].tofile(folder/(name+".bin"))


def export_dev(folder,pcm,arrays,case):
    nf=case["frames"]
    meta=np.asarray([[i,int(arrays["frame_starts"][i]),int(arrays["shift_s"][i]),
                       -27-2*int(arrays["shift_s"][i]),int(arrays["mel_exponent"][i]),
                       int(arrays["fft_overflow"][i]),int(arrays["input_clips"][i]),int(arrays["bfp_clamped"][i])]
                      for i in range(nf)],dtype="<i4")
    (folder/"c_fixed_full_vectors.h").write_text(
        '#ifndef C_FIXED_FULL_VECTORS_H\n#define C_FIXED_FULL_VECTORS_H\n#include <stdint.h>\n'
        f'#define C_FIXED_FULL_DEV_SAMPLES {len(pcm)}U\n#define C_FIXED_FULL_DEV_FRAMES {nf}U\n'
        '#define C_FIXED_FULL_DEV_PCM_SHA256 "'+case["pcm"]["sha256"]+'"\n'
        '/* metadata: frame_id,start_sample,s,power_exp2,mel_exp2,fft_overflow,input_clips,bfp_clamped */\n'
        'extern const int16_t c_fixed_full_dev_pcm[C_FIXED_FULL_DEV_SAMPLES];\n'
        'extern const int64_t c_fixed_full_dev_mfcc[C_FIXED_FULL_DEV_FRAMES][13];\n'
        'extern const int32_t c_fixed_full_dev_metadata[C_FIXED_FULL_DEV_FRAMES][8];\n#endif\n',encoding="ascii")
    (folder/"c_fixed_full_vectors.c").write_text(
        '#include "c_fixed_full_vectors.h"\nconst int16_t c_fixed_full_dev_pcm[C_FIXED_FULL_DEV_SAMPLES] = '+c_array(pcm)+';\n'
        'const int64_t c_fixed_full_dev_mfcc[C_FIXED_FULL_DEV_FRAMES][13] = {\n'+',\n'.join(c_array(row,"LL") for row in arrays["mfcc_q24"])+ '\n};\n'
        'const int32_t c_fixed_full_dev_metadata[C_FIXED_FULL_DEV_FRAMES][8] = {\n'+',\n'.join(c_array(row) for row in meta)+ '\n};\n',encoding="ascii")


def prepare(package,out):
    contract,report=audit(package)
    dump(out/"published_contract_audit.json",report)
    frozen=out/"published_contract"; shutil.copytree(package,frozen)
    full,fft=import_model(frozen/"model_snapshot")
    coefficient={name:descriptor(frozen,entry) for name,entry in contract["coefficients"].items() if "dtype" in entry}
    twiddle=fft.load_twiddle_rom(frozen/contract["coefficients"]["twiddle_q15"]["file"])
    folder=out/"contract";folder.mkdir()
    export_tables(folder,coefficient,twiddle)
    ref_index=read(REFERENCE/"artifact_hashes.json")
    used_reference={}
    def checked_reference(relative):
        path=REFERENCE/relative
        if relative not in ref_index or sha(path)!=ref_index[relative]:raise ValueError("Frozen float64 reference mismatch")
        used_reference[relative]=sha(path)
        return path
    shutil.copyfile(checked_reference("arrays/frozen_coefficients.npz"),folder/"frozen_float64_coefficients.npz")
    shutil.copyfile(checked_reference("source_snapshot/docs/MFCC_SPEC.md"),folder/"MFCC_SPEC_snapshot.md")
    expected_frames=[];expected_pre=[];expected_states=[];cases=[];model_checks=0
    with (folder/"input.bin").open("wb") as input_file:
        input_file.write(b"CFUL0002"+struct.pack("<I",len(contract["cases"])))
        for ordinal,case in enumerate(contract["cases"]):
            pcm=descriptor(frozen,case["pcm"])
            arrays={name:descriptor(frozen,entry) for name,entry in case["stages"].items()}
            reproduced=full.run_pcm(pcm.tolist(),{k:v.tolist() for k,v in coefficient.items()},twiddle)
            for name,gold in arrays.items():
                got=np.asarray(reproduced[name],dtype=gold.dtype).reshape(gold.shape)
                if not np.array_equal(got,gold):raise ValueError("Published full-model replay mismatch: "+case["id"]+"/"+name)
                model_checks+=gold.size
            nf=case["frames"];samples=len(pcm)
            if nf != (0 if samples<512 else 1+(samples-512)//160):raise ValueError("Published framing mismatch")
            if not np.array_equal(arrays["frame_starts"],np.arange(nf)*160):raise ValueError("Frame start mismatch")
            pre_start=len(expected_pre); frame_start=len(expected_frames)
            expected_pre.extend(int(v) for v in arrays["preemphasis_q30"])
            ring=[0]*512
            for i,value in enumerate(arrays["preemphasis_q30"]):ring[i%512]=int(value)
            remaining=512-samples if samples<512 else 160-(samples-512)%160
            expected_states.append([ordinal,samples,nf,int(pcm[-1]) if samples else 0,samples%512,remaining,0,1,1,*ring])
            for f in range(nf):
                metadata=[ordinal,f,int(arrays["frame_starts"][f]),int(arrays["shift_s"][f]),
                          -27-2*int(arrays["shift_s"][f]),int(arrays["mel_exponent"][f]),
                          int(arrays["fft_overflow"][f]),0,0,int(arrays["input_clips"][f]),int(arrays["bfp_clamped"][f])]
                row=[*metadata]
                for name in LAYOUT:
                    if name!="metadata":row.extend(int(v) for v in arrays[name][f])
                if len(row)!=FRAME_COLUMNS:raise AssertionError("Internal frame layout")
                expected_frames.append(row)
            input_file.write(struct.pack("<I",samples));input_file.write(pcm.astype("<i2").tobytes())
            # Same published PCM identity binds the historical float64 reference.
            reference_pcm=checked_reference("arrays/"+case["id"]+"_s16le.pcm")
            if sha(reference_pcm)!=case["pcm"]["sha256"]:raise ValueError("Float64/contract PCM differs")
            ref=checked_reference("arrays/"+case["id"]+"_t0p975_A_float64_reference_stages.npz")
            shutil.copyfile(ref,folder/ref.name)
            cases.append({"ordinal":ordinal,"id":case["id"],"group":case["group"],"samples":samples,"frames":nf,
                          "first_pre":pre_start,"first_frame":frame_start,"previous_pcm":int(pcm[-1]) if samples else 0,
                          "pcm_sha256":case["pcm"]["sha256"],"reference_file":ref.name})
            if case["group"]=="development":export_dev(folder,pcm,arrays,case)
            print(f"v2 frozen replay {case['id']}: {samples} PCM, {nf} full frames",flush=True)
    np.asarray(expected_pre,dtype="<i8").tofile(folder/"expected_pre_i64le.bin")
    np.asarray(expected_frames,dtype="<i8").tofile(folder/"expected_frames_i64le.bin")
    np.asarray(expected_states,dtype="<i8").tofile(folder/"expected_states_i64le.bin")
    dump(folder/"cases.json",cases)
    fixture={"version":VERSION,"scope":"full integer PCM16 to raw13 signed40/F24",
             "published_contract_sha256":sha(frozen/"contract.json"),
             "published_artifact_index_sha256":sha(frozen/"artifact_hashes.json"),
             "source_hashes":report["source_hashes"],"spec_sha256":sha(folder/"MFCC_SPEC_snapshot.md"),
             "float64_reference_run":str(REFERENCE),"float64_reference_hashes":used_reference,
             "model_replay_integer_components":model_checks,"cases":len(cases),"frames":len(expected_frames),
             "development_vector":{"case":cases[0]["id"],"samples":cases[0]["samples"],"frames":cases[0]["frames"],"pcm_sha256":cases[0]["pcm_sha256"]},
             "pcm_samples":len(expected_pre),"frame_columns":FRAME_COLUMNS,"layout":LAYOUT,
             "frame_metadata":"case,frame_id,start_sample,s,power_exp2,mel_exp2,fft_ov,power_ov,mel_ov,input_clips,bfp_clamped",
             "algorithm_accuracy":"NOT_ACCEPTED; tolerance unpublished",
             "generated_sha256":{p.name:sha(p) for p in sorted(folder.iterdir()) if p.is_file()}}
    dump(folder/"contract.json",fixture)
    digest=sha(folder/"contract.json")
    (folder/"c_fixed_contract.h").write_text('#ifndef C_FIXED_CONTRACT_H\n#define C_FIXED_CONTRACT_H\n'
       '#define C_FIXED_CONTRACT_VERSION "'+VERSION+'"\n#define C_FIXED_CONTRACT_SHA256 "'+digest+'"\n'
       '#define C_FIXED_PUBLISHED_CONTRACT_SHA256 "'+fixture["published_contract_sha256"]+'"\n#endif\n',encoding="ascii")
    dump(out/"prepare.json",{"status":"prepared","contract_version":VERSION,"contract_sha256":digest,
                             "published_contract_sha256":fixture["published_contract_sha256"],"frames":len(expected_frames),
                             "cases":len(cases),"pcm_samples":len(expected_pre),"C_executed":False})
    return fixture


def compare_actual(pre_file,frame_file,out):
    expected_pre=np.fromfile(out/"contract/expected_pre_i64le.bin",dtype="<i8")
    expected=np.fromfile(out/"contract/expected_frames_i64le.bin",dtype="<i8").reshape(-1,FRAME_COLUMNS)
    pre=np.fromfile(pre_file,dtype="<i8");flat=np.fromfile(frame_file,dtype="<i8")
    if pre.size!=expected_pre.size or flat.size!=expected.size:raise ValueError("C output size mismatch")
    actual=flat.reshape(expected.shape)
    stages={"preemphasis_q30":{"values":int(pre.size),"mismatches":int(np.count_nonzero(pre!=expected_pre))}}
    for name,(start,end) in LAYOUT.items():
        mismatch=actual[:,start:end]!=expected[:,start:end]
        stages[name]={"values":int(mismatch.size),"mismatches":int(np.count_nonzero(mismatch))}
        if np.any(mismatch):
            f,i=np.argwhere(mismatch)[0]
            stages[name]["first"]={"frame":int(f),"column":int(i),"expected":int(expected[f,start+i]),"actual":int(actual[f,start+i])}
    return actual,pre,{"passed":not any(v["mismatches"] for v in stages.values()),"stages":stages,
                       "pre_sha256":sha(pre_file),"frames_sha256":sha(frame_file)}


def numerical(actual,pre,out):
    cases=read(out/"contract/cases.json");evidence=read(out/"published_contract/numerical_evidence.json")
    coeff=np.load(out/"contract/frozen_float64_coefficients.npz",allow_pickle=False)
    report={"scope":"Host-only reconstruction of complete integer C outputs versus immutable float64 reference",
            "algorithm_accuracy":"NOT_ACCEPTED","cases":[],"aggregate":{}}
    for case in cases:
        first,nf=case["first_frame"],case["frames"];rows=actual[first:first+nf]
        ref=np.load(out/"contract"/case["reference_file"],allow_pickle=False)
        pubcase=next(c for c in read(out/"published_contract/contract.json")["cases"] if c["id"]==case["id"])
        pcm=descriptor(out/"published_contract",pubcase["pcm"])
        x=pcm.astype(float)/32768.0;y=np.zeros_like(x)
        if x.size:y[0]=x[0];y[1:]=x[1:]-0.95*x[:-1]
        windows=np.asarray([y[f*160:f*160+512]*coeff["window"] for f in range(nf)]).reshape(nf,512)
        values={"preemphasis":(pre[case["first_pre"]:case["first_pre"]+case["samples"]].astype(float)*2.0**-30,y),
                "windowed":(rows[:,11:523].astype(float)*2.0**-30,windows),
                "fft":((rows[:,1035:1292]+1j*rows[:,1547:1804])*np.exp2((-9-rows[:,3])[:,None]),ref["fft"]),
                "power":(rows[:,2059:2316].astype(float)*np.exp2(rows[:,4,None]),ref["power"]),
                "mel":(rows[:,2316:2342].astype(float)*np.exp2(rows[:,5,None]),ref["mel_energies"]),
                "log":(rows[:,2342:2368].astype(float)*2.0**-24,ref["log_mel"]),
                "mfcc":(rows[:,2394:2407].astype(float)*2.0**-24,ref["mfcc"])}
        mel=values["mel"][0];floor=int(np.sum((ref["mel_energies"]>1e-12)&(mel<1e-12)))
        result={"id":case["id"],"group":case["group"],"frames":nf,"floor_regressions":floor,"stages":{}}
        group="development" if case["group"]=="development" else "all_synthetic"
        aggregate=report["aggregate"].setdefault(group,{"frames":0,"floor_regressions":0,"stages":{}})
        aggregate["frames"]+=nf;aggregate["floor_regressions"]+=floor
        for name,(got,gold) in values.items():
            diff=np.abs(got-gold);n=diff.size;sse=float(np.sum(diff**2))
            metric={"max_abs":float(np.max(diff)) if n else 0.0,"rmse":float(np.sqrt(sse/n)) if n else 0.0,"elements":n,"sse":sse}
            result["stages"][name]=metric
            target=aggregate["stages"].setdefault(name,{"max_abs":0.0,"sse":0.0,"elements":0})
            target["max_abs"]=max(target["max_abs"],metric["max_abs"]);target["sse"]+=sse;target["elements"]+=n
        d=np.abs(values["mfcc"][0]-values["mfcc"][1])
        result["raw13_per_coefficient"]=[{"coefficient":i,"max_abs":float(np.max(d[:,i])) if nf else 0.0,
                "rmse":float(np.sqrt(np.mean(d[:,i]**2))) if nf else 0.0} for i in range(13)]
        published=next(c for c in evidence["cases"] if c["id"]==case["id"])
        result["published_metric_difference"]={name:{metric:result["stages"][name][metric]-published["errors"][name][metric]
                                                       for metric in ("max_abs","rmse")}
                                                for name in ("windowed","mel","log","mfcc")}
        if floor!=published["floor_regressions"]:raise ValueError("Published floor regression mismatch")
        if any(abs(v)>1e-10 for differences in result["published_metric_difference"].values() for v in differences.values()):
            raise ValueError("Published numerical diagnostic consistency check failed")
        report["cases"].append(result)
    for aggregate in report["aggregate"].values():
        for metric in aggregate["stages"].values():
            metric["rmse"]=(metric["sse"]/metric["elements"])**0.5 if metric["elements"] else 0.0
    return report
