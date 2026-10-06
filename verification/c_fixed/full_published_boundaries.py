"""Replay exact published v2 helper boundaries against three C compilers.

Adds a fresh supplemental directory; existing PC run evidence is never edited.
The C source is generated from published expected integers, not recomputed gold.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode=True
from full_reference import sha,read,dump,c_array


def generated_test(data):
    log_rows=",\n".join("{%dULL,%d,%d,%dU}"%(v["T"],v["exponent"],v["expected"],v["floor"]) for v in data["log"])
    bfp_rows=",\n".join("{%d,%d,%dU,%d,%d,%dU}"%(v["peak_q30"],v["expected_s"],v["bfp_clamped"],*v["expected_q"],v["clips"]) for v in data["bfp"])
    return '''#include "mfcc_fixed_full.h"
#include "c_fixed_full_tables.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
static unsigned checks,failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"line %d failed\\n",__LINE__); } } while(0)
struct log_case { uint64_t t; int32_t exponent,expected; uint32_t floored; };
static const struct log_case logs[] = {
'''+log_rows+'''
};
struct bfp_case { int32_t peak,shift; uint32_t clamped; int16_t real,negative; uint32_t clips; };
static const struct bfp_case bfps[] = {
'''+bfp_rows+'''
};
static const int16_t pre_pcm[] = '''+c_array(data["preemphasis"]["pcm"])+''';
static const int32_t pre_expected[] = '''+c_array(data["preemphasis"]["expected"])+''';
static const int32_t dct_inputs[3][26] = {
'''+",\n".join(c_array(v["log_q24"]) for v in data["dct"])+'''
};
static const int64_t dct_expected[3][13] = {
'''+",\n".join(c_array(v["expected"],"LL") for v in data["dct"])+'''
};
int main(void)
{
    unsigned i,k; int32_t q,s;uint32_t f,clamp,clips;
    int32_t window[512]; int16_t quantized[512];int64_t dct[13];
    for(i=0;i<sizeof logs/sizeof logs[0];++i){
        CHECK(cf_full_log(logs[i].t,logs[i].exponent,&q,&f)==CF_FULL_OK);
        CHECK(q==logs[i].expected);CHECK(f==logs[i].floored);
        CHECK(cf_full_floor(logs[i].t,logs[i].exponent,&f)==CF_FULL_OK);CHECK(f==logs[i].floored);
    }
    for(i=0;i<sizeof bfps/sizeof bfps[0];++i){
        memset(window,0,sizeof window);window[0]=bfps[i].peak;window[1]=-bfps[i].peak;
        CHECK(cf_full_choose_bfp(window,&s,&clamp)==CF_FULL_OK);
        CHECK(s==bfps[i].shift);CHECK(clamp==bfps[i].clamped);
        CHECK(cf_full_quantize(window,s,quantized,&clips)==CF_FULL_OK);
        CHECK(clips==bfps[i].clips);CHECK(quantized[0]==bfps[i].real);CHECK(quantized[1]==bfps[i].negative);
        for(k=2;k<512U;++k)CHECK(quantized[k]==0);
    }
    for(i=0;i<sizeof pre_pcm/sizeof pre_pcm[0];++i){
        CHECK(cf_full_preemphasis(pre_pcm[i],i?pre_pcm[i-1]:0,&q)==CF_FULL_OK);
        CHECK(q==pre_expected[i]);
    }
    for(i=0;i<3U;++i){
        CHECK(cf_full_dct(&c_fixed_full_tables,dct_inputs[i],dct)==CF_FULL_OK);
        for(k=0;k<13U;++k)CHECK(dct[k]==dct_expected[i][k]);
    }
    CHECK(cf_full_log(UINT64_MAX,-43,&q,&f)==CF_FULL_LOG_RANGE);
    CHECK(cf_full_log(UINT64_C(1)<<60U,-43,&q,&f)==CF_FULL_LOG_RANGE);
    CHECK(cf_full_log(1U,-38,&q,&f)==CF_FULL_BAD_EXPONENT);
    CHECK(cf_full_log(1U,-92,&q,&f)==CF_FULL_BAD_EXPONENT);
    CHECK(cf_full_log(1U,0,&q,&f)==CF_FULL_BAD_EXPONENT);
    CHECK(cf_full_quantize(window,-3,quantized,&clips)==CF_FULL_BAD_EXPONENT);
    CHECK(cf_full_quantize(window,25,quantized,&clips)==CF_FULL_BAD_EXPONENT);
    printf("{\\"checks\\":%u,\\"failures\\":%u,\\"log_vectors\\":214,\\"bfp_vectors\\":75,\\"preemphasis_samples\\":8,\\"dct_vectors\\":3,\\"invalid_domain_checks\\":7}\\n",checks,failures);
    return failures?1:0;
}
'''


def run_boundary_audit(out,identifier="published_boundaries_01"):
    out=Path(out).resolve();folder=out/identifier
    if Path(identifier).name!=identifier or not identifier.startswith("published_boundaries_"):raise ValueError("Safe boundary identifier required")
    folder.mkdir(exist_ok=False)
    fixture=read(out/"contract/contract.json");prepared=read(out/"prepare.json")
    if sha(out/"contract/contract.json")!=prepared["contract_sha256"]:raise ValueError("Fixture identity changed")
    package=out/"published_contract"
    if sha(package/"contract.json")!=fixture["published_contract_sha256"]:raise ValueError("Publication identity changed")
    index=read(package/"artifact_hashes.json")
    relative="vectors/boundaries.json"
    if sha(package/relative)!=index[relative]:raise ValueError("Boundary truth hash changed")
    data=read(package/relative)
    test=folder/"test_published.c";test.write_text(generated_test(data),encoding="ascii")
    shutil.copyfile(Path(__file__),folder/"full_published_boundaries.py")
    source=out/"source_snapshot/software/c_fixed"
    source_hashes=read(out/"source_hashes.json")
    for name in ("fixed_int.c","fixed_int.h","mfcc_fixed.c","mfcc_fixed.h","mfcc_fixed_full.c","mfcc_fixed_full.h"):
        if sha(source/name)!=source_hashes["software/c_fixed/"+name]:raise ValueError("Pinned core changed")
    for name in ("c_fixed_tables.c","c_fixed_tables.h","c_fixed_full_tables.c","c_fixed_full_tables.h"):
        if sha(out/"contract"/name)!=fixture["generated_sha256"][name]:raise ValueError("Pinned coefficients changed")
    project=Path(__file__).resolve().parents[2]
    runner=project/"scripts/run_c_fixed_full.py"
    spec=importlib.util.spec_from_file_location("c_fixed_full_build_helpers",runner)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    cc,env=helper.msvc_environment(folder)
    clang=Path(r"C:\Xilinx\Vitis\2024.2\vcxx\libexec\clang.exe")
    builds=[("msvc_O2",cc,["/nologo","/TC","/std:c11","/O2","/W4","/WX","/MD"],env),
            ("msvc_RTC",cc,["/nologo","/TC","/std:c11","/Od","/RTC1","/W4","/WX","/MD"],env),
            ("clang_ubsan_O2",clang,["--sysroot=C:/Xilinx/Vitis/2024.2/vcxx","-fuse-ld=lld","-std=c11","-O2",
               "-Wall","-Wextra","-Werror","-Wconversion","-Wshadow","-fsanitize=undefined","-fno-sanitize-recover=all"],None)]
    common=[source/f for f in ("fixed_int.c","mfcc_fixed.c","mfcc_fixed_full.c")]
    common += [out/"contract/c_fixed_tables.c",out/"contract/c_fixed_full_tables.c",test]
    results=[]
    for name,compiler,flags,cenv in builds:
        build=folder/name;build.mkdir();exe=build/"test.exe"
        if name.startswith("msvc"):
            command=[compiler,*flags,"/I"+str(source),"/I"+str(out/"contract"),"/Fo"+str(build)+os.sep,"/Fe"+str(exe),*common]
        else:command=[compiler,*flags,"-I"+str(source),"-I"+str(out/"contract"),*common,"-o",exe]
        helper.checked(command,build,build/"compile.log",cenv)
        result=helper.checked([exe],build,build/"run.log",cenv)
        results.append({"build":name,"compiler_sha256":sha(compiler),"executable_sha256":sha(exe),
                        "command":[str(x) for x in command],"result":json.loads(result.stdout.strip())})
    report={"status":"passed","contract_sha256":fixture["published_contract_sha256"],"fixture_sha256":prepared["contract_sha256"],
            "boundary_source_sha256":sha(package/relative),"generated_c_sha256":sha(test),
            "auditor_sha256":sha(Path(__file__)),"build_helper_sha256":sha(runner),
            "full_reference_helper_sha256":sha(project/"verification/c_fixed/full_reference.py"),
            "core_sha256":{name:sha(source/name) for name in ("fixed_int.c","mfcc_fixed.c","mfcc_fixed_full.c")},
            "builds":results,"algorithm_accuracy":"NOT_ACCEPTED"}
    dump(folder/"report.json",report)
    dump(folder/"artifact_hashes.json",{p.relative_to(folder).as_posix():sha(p) for p in folder.rglob("*") if p.is_file() and p.name!="artifact_hashes.json"})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run",type=Path);parser.add_argument("--id",default="published_boundaries_01")
    args=parser.parse_args();result=run_boundary_audit(args.run,args.id)
    print(json.dumps(result,indent=2))


if __name__=="__main__":main()
