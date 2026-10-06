"""Independent published-v1 audit and C utility boundary replay.

Only ties-to-even and signed wrapping are executed in C. Published BFP
selection and floor cases are recorded as not implemented by this C core.
The fresh output directory retains the audit script, package hashes, C source,
compiler command/log, executable and result. No reference input is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import numpy as np

PROJECT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def audit(package, prior, provisional):
    pub = read(package / "PUBLISHED.json")
    contract = read(package / "contract.json")
    index = read(package / "artifact_hashes.json")
    failures = []
    for relative, expected in index.items():
        path = (package / relative).resolve()
        if not path.is_relative_to(package.resolve()) or digest(path) != expected:
            failures.append(relative)
    markers = {field: digest(package / filename) == pub[field]
               for field, filename in (("contract_sha256", "contract.json"),
                                       ("artifact_manifest_sha256", "artifact_hashes.json"),
                                       ("verification_sha256", "verification.json"))}
    sources = {}
    for relative, expected in contract["source_sha256"].items():
        old = prior / "source_snapshot" / relative
        sources[relative] = {
            "sha256": expected,
            "published_hash_ok": digest(package / "model_snapshot" / relative) == expected,
            "same_as_prec04": digest(old) == expected if old.exists() else None,
        }
    vectors = {}
    for key, info in contract["vector_files"].items():
        path = package / info["file"]
        if digest(path) != info["sha256"] or path.stat().st_size != info["bytes"]:
            raise ValueError("Published vector integrity mismatch: " + key)
        vectors[key] = np.fromfile(path, dtype=info["dtype"]).reshape(info["shape"])
    previous = read(provisional / "contract/contract.json")
    for relative, expected in previous["generated_sha256"].items():
        if digest(provisional / "contract" / relative) != expected:
            raise ValueError("Provisional artifact changed: " + relative)
    expected = np.fromfile(provisional / "contract/expected_i64le.bin", dtype="<i8").reshape(
        previous["records"], previous["values_per_record"])
    normal = contract["normal_frames"]
    comparisons = {}
    for name, first, last in (("fft_real", 6, 518), ("fft_imag", 518, 1030),
                              ("power", 1030, 1287), ("mel", 1287, 1313)):
        compared = vectors[name][:normal] != expected[:normal, first:last]
        comparisons[name] = {"values": int(compared.size), "mismatches": int(np.count_nonzero(compared))}
    for name, new_column, old_column in (("bfp", 1, 0), ("overflow", 2, 3),
                                        ("power_exp2", 3, 1), ("mel_exp2", 4, 2)):
        compared = vectors["meta"][:normal, new_column] != expected[:normal, old_column]
        comparisons[name] = {"values": normal, "mismatches": int(np.count_nonzero(compared))}
    comparisons["frame_id"] = {"values": contract["total_frames"], "mismatches": int(np.count_nonzero(
        vectors["meta"][:, 0] != np.arange(contract["total_frames"])))}
    dtype = np.dtype([("s", "<i4"), ("re", "<i2", (512,)), ("im", "<i2", (512,))])
    old_input = np.fromfile(provisional / "contract/input.bin", dtype=dtype, offset=12)
    for name, old_column in (("input_real", "re"), ("input_imag", "im")):
        compared = vectors[name][:normal] != old_input[old_column][:normal]
        comparisons[name] = {"values": int(compared.size), "mismatches": int(np.count_nonzero(compared))}
    coefficient_equality = {
        "mel_u32le.bin": (package / "coefficients/mel_u32le.bin").read_bytes() ==
                        (provisional / "contract/mel_u32le.bin").read_bytes(),
        "twiddle_1024_w16.mem": (package / "coefficients/twiddle_1024_w16.mem").read_bytes() ==
                               (provisional / "contract/twiddle_1024_w16.mem").read_bytes(),
    }
    passed = (not failures and pub["status"] == "PUBLISHED" and all(markers.values())
              and all(x["published_hash_ok"] and x["same_as_prec04"] is not False for x in sources.values())
              and all(x["mismatches"] == 0 for x in comparisons.values())
              and all(coefficient_equality.values()))
    return {"status": "PASS" if passed else "FAIL", "package": str(package),
            "version": pub["version"], "contract_sha256": pub["contract_sha256"],
            "publication_marker_sha256": digest(package / "PUBLISHED.json"),
            "artifact_count": len(index), "artifact_failures": failures,
            "publication_markers": markers, "sources": sources,
            "coefficients_same_as_provisional": coefficient_equality,
            "normal_frames": normal, "all512_direct_comparisons": comparisons,
            "published_overflow_frame_ids": np.flatnonzero(vectors["meta"][:, 2]).tolist(),
            "provisional_expected_sha256": digest(provisional / "contract/expected_i64le.bin"),
            "not_a_new_C_execution": True, "numerical_accuracy_accepted": False}


def boundary_test(package, output):
    boundaries = read(package / "vectors/boundaries.json")
    snapshot = output / "source_snapshot"
    snapshot.mkdir()
    for name in ("fixed_int.c", "fixed_int.h"):
        shutil.copyfile(PROJECT / "software/c_fixed" / name, snapshot / name)
    rounding = ",\n".join("{%sLL,%sU,%sLL}" % (r["value"], r["shift"], r["expected"])
                            for r in boundaries["rounding"])
    wrapping = ",\n".join("{%sLL,%sLL,%s}" % (r["value"], r["wrapped"], int(r["overflow"]))
                           for r in boundaries["signed20_boundaries"])
    source = '''#include "fixed_int.h"
#include <stdio.h>
static const struct { int64_t value; unsigned shift; int64_t expected; } rounds[] = {
%s
};
static const struct { int64_t value; int64_t expected; int overflow; } wraps[] = {
%s
};
int main(void) {
    unsigned i, checks = 0, failures = 0;
    for(i=0;i<sizeof(rounds)/sizeof(rounds[0]);++i) {
        fxi_signed a=fxi_round_even(rounds[i].value,rounds[i].shift);
        ++checks;
        if(!a.valid || a.overflow || a.value!=rounds[i].expected) {
            ++failures; fprintf(stderr,"rounding row %%u failed\\n",i);
        }
    }
    for(i=0;i<sizeof(wraps)/sizeof(wraps[0]);++i) {
        fxi_signed a=fxi_wrap_signed(wraps[i].value,20);
        ++checks;
        if(!a.valid || a.value!=wraps[i].expected || a.overflow!=(wraps[i].overflow!=0)) {
            ++failures; fprintf(stderr,"wrap row %%u failed\\n",i);
        }
    }
    printf("published_boundary_checks=%%u failures=%%u\\n",checks,failures);
    return failures ? 1 : 0;
}
''' % (rounding, wrapping)
    main = snapshot / "published_boundary_main.c"
    main.write_text(source, encoding="ascii")
    compiler = Path(r"C:\Xilinx\Vitis\2024.2\vcxx\libexec\clang.exe")
    exe = output / "published_boundaries.exe"
    command = [str(compiler), "--sysroot=C:/Xilinx/Vitis/2024.2/vcxx", "-fuse-ld=lld",
               "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wconversion",
               "-fsanitize=undefined", "-fno-sanitize-recover=all", str(snapshot / "fixed_int.c"),
               str(main), "-o", str(exe)]
    compiled = subprocess.run(command, cwd=output, capture_output=True, text=True)
    (output / "compile.log").write_text(json.dumps(command) + "\n" + compiled.stdout + compiled.stderr +
                                       "\nEXIT " + str(compiled.returncode), encoding="utf-8")
    if compiled.returncode:
        raise RuntimeError("Boundary compilation failed; compile.log retained")
    ran = subprocess.run([str(exe)], cwd=output, capture_output=True, text=True)
    (output / "run.log").write_text(ran.stdout + ran.stderr + "\nEXIT " + str(ran.returncode), encoding="utf-8")
    result = {"status": "PASS" if ran.returncode == 0 else "FAIL", "exit_code": ran.returncode,
              "checks": len(boundaries["rounding"]) + len(boundaries["signed20_boundaries"]),
              "coverage": {"rounding": len(boundaries["rounding"]),
                           "signed20_boundaries": len(boundaries["signed20_boundaries"])},
              "not_implemented_in_C": ["BFP selection", "floor/log"],
              "boundaries_sha256": digest(package / "vectors/boundaries.json"),
              "compiler": str(compiler), "compiler_sha256": digest(compiler),
              "compiler_command": command, "executable_sha256": digest(exe),
              "source_sha256": {p.name: digest(p) for p in sorted(snapshot.iterdir())}}
    save(output / "published_boundary_result.json", result)
    if ran.returncode:
        raise RuntimeError("Boundary run failed; result/log retained")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--contract", type=Path, default=Path(
        r"D:\2610_MFCC\build\fixed_contract\v1_fft20_power40_mel60_20261004_r2"))
    parser.add_argument("--prior", type=Path, default=Path(
        r"D:\2610_MFCC\build\fft_precision\prec_04_verified_full_20261004"))
    parser.add_argument("--provisional", type=Path, default=Path(r"D:\2610_MFCC\build\c_fixed_20261004_01"))
    args = parser.parse_args()
    if not args.run_id.startswith("c_fixed") or Path(args.run_id).name != args.run_id:
        parser.error("A new single folder name beginning c_fixed is required")
    output = PROJECT.parent / "build" / args.run_id
    output.mkdir(exist_ok=False)
    shutil.copyfile(__file__, output / Path(__file__).name)
    result = audit(args.contract, args.prior, args.provisional)
    result["audit_script_sha256"] = digest(output / Path(__file__).name)
    save(output / "published_contract_audit.json", result)
    if result["status"] != "PASS":
        raise RuntimeError("Published contract audit failed; result retained")
    boundaries = boundary_test(args.contract, output)
    save(output / "artifact_hashes.json", {str(p.relative_to(output)).replace("\\", "/"): digest(p)
         for p in sorted(output.rglob("*")) if p.is_file() and p.name != "artifact_hashes.json"})
    print(json.dumps({"audit": result["status"], "boundaries": boundaries["status"],
                      "boundary_checks": boundaries["checks"], "output": str(output)}))


if __name__ == "__main__":
    main()
