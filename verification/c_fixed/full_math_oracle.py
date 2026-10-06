"""Independent Python-bigint oracle for portable v2 wide-log arithmetic.

Creates an exclusive run, snapshots the tested C, and builds/runs MSVC O2 and
Clang UBSan. No live Python MFCC model, floating arithmetic, or gold edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[2]
CONTRACT_HASH = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"
LN2 = 744261118
FLOOR_NUM = 4951760157141521
FLOOR_DEN = 4951760157141521099596496896


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run(command, cwd, log, env=None):
    command = [str(x) for x in command]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, errors="replace")
    log.write_text(json.dumps(command) + "\n" + result.stdout + "\n" + result.stderr
                   + "\nEXIT " + str(result.returncode) + "\n", encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Failed: " + str(log))
    return result.stdout


def expected_scale(a):
    # This oracle uses the full signed67 product, not the C h/l decomposition.
    quotient, remainder = divmod(a * LN2, 1 << 36)
    return quotient + int(2 * remainder > (1 << 36) or
                          (2 * remainder == (1 << 36) and quotient % 2 != 0))


def fixture(path):
    rng = random.Random(20261004)
    low, high = -(1 << 36), (1 << 36) - 1
    values = set(range(-4096, 4097))
    for endpoint in (low, high, -(1 << 35), 1 << 35):
        values.update(a for a in range(endpoint - 256, endpoint + 257) if low <= a <= high)
    # Solve LN2*a modulo 2^36 around the exact halfway residue. This targets
    # the low6-bit contribution that an incorrectly split rounding loses.
    inverse = pow(LN2 // 2, -1, 1 << 35)
    for offset in range(-128, 129):
        remainder = (1 << 35) + 2 * offset
        residue = (remainder // 2 * inverse) % (1 << 35)
        for k in range(-3, 3):
            a = residue + k * (1 << 35)
            if low <= a <= high:
                values.add(a)
    for _ in range(250000):
        values.add(rng.randrange(low, high + 1))
    exact_ties = above_split_ties = below_split_ties = wide = 0
    with path.open("x", encoding="ascii", newline="\n") as stream:
        for a in sorted(values):
            product = a * LN2
            remainder = product % (1 << 36)
            exact_ties += remainder == 1 << 35
            above_split_ties += (1 << 35) < remainder < (1 << 35) + 64
            below_split_ties += (1 << 35) - 64 < remainder < (1 << 35)
            wide += not -(1 << 63) <= product < (1 << 63)
            stream.write(f"S {a} {expected_scale(a)}\n")
        floor_checks = 0
        for exponent in range(-91, -38, 2):
            threshold = (FLOOR_NUM << -exponent) // FLOOR_DEN
            # Assert the contract's decimal threshold reduction independently.
            assert threshold == (1 << -exponent) // 10**12
            mel_values = {0, 1, (1 << 60) - 1}
            mel_values.update(t for t in range(threshold - 128, threshold + 129)
                              if 0 <= t < (1 << 60))
            for bit in range(60):
                mel_values.update(t for t in ((1 << bit) - 1, 1 << bit, (1 << bit) + 1)
                                  if t < (1 << 60))
            mel_values.update(rng.randrange(1 << 60) for _ in range(512))
            for mel in sorted(mel_values):
                floored = int(mel * FLOOR_DEN <= FLOOR_NUM * (1 << -exponent))
                stream.write(f"F {mel} {exponent} {floored}\n")
                floor_checks += 1
    assert exact_ties >= 4 and above_split_ties >= 124 and wide > 100000
    return {"scale_checks": len(values), "floor_checks": floor_checks,
            "exact_halfway_products": exact_ties,
            "just_above_halfway_low6_nonzero": above_split_ties,
            "just_below_halfway_products": below_split_ties,
            "products_outside_signed64": wide, "seed": 20261004}


def msvc_environment(folder):
    vcvars = r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
    capture = folder / "capture_environment.cmd"
    capture.write_text('@echo off\ncall "' + vcvars + '" >nul\nif errorlevel 1 exit /b 1\nset\n',
                       encoding="ascii")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(capture)], capture_output=True,
                            text=True, errors="replace", check=True)
    env = os.environ.copy()
    compiler = None
    compiler_path = None
    for line in result.stdout.splitlines():
        if "=" in line and not line.startswith("="):
            key, value = line.split("=", 1)
            env[key] = value
            if key.upper() == "PATH" and shutil.which("cl.exe", path=value):
                compiler_path = value
                compiler = shutil.which("cl.exe", path=value)
    for key in ("CL", "_CL_", "LINK"):
        env.pop(key, None)
    if not compiler:
        raise RuntimeError("MSVC unavailable")
    for key in list(env):
        if key.upper() == "PATH":
            del env[key]
    env["PATH"] = compiler_path
    return compiler, env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    assert sha(args.contract / "contract.json") == CONTRACT_HASH
    snapshot = args.out / "source_snapshot"
    sources = ["software/c_fixed/" + name for name in (
        "fixed_int.c", "fixed_int.h", "mfcc_fixed.c", "mfcc_fixed.h",
        "mfcc_fixed_full.c", "mfcc_fixed_full.h")]
    sources += ["verification/c_fixed/full_math_probe.c", "verification/c_fixed/full_math_oracle.py"]
    hashes = {}
    for relative in sources:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / relative, target)
        hashes[relative] = sha(target)
    dump(args.out / "source_hashes.json", hashes)
    vectors = args.out / "wide_log_cases.txt"
    coverage = fixture(vectors)
    builds = {}
    include = snapshot / "software/c_fixed"
    c_sources = [include / n for n in ("fixed_int.c", "mfcc_fixed.c", "mfcc_fixed_full.c")]
    c_sources += [snapshot / "verification/c_fixed/full_math_probe.c"]
    compiler, env = msvc_environment(args.out)
    for name in ("msvc_o2", "clang_ubsan"):
        folder = args.out / name
        folder.mkdir()
        exe = folder / "wide_log_probe.exe"
        if name == "msvc_o2":
            command = [compiler, "/nologo", "/std:c11", "/O2", "/W4", "/WX", "/I" + str(include),
                       *c_sources, "/Fe:" + str(exe)]
            build_env = env
        else:
            command = [r"C:\Xilinx\Vitis\2024.2\vcxx\libexec\clang.exe",
                       "--sysroot=C:/Xilinx/Vitis/2024.2/vcxx", "-fuse-ld=lld", "-std=c11", "-O2",
                       "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wsign-conversion",
                       "-fsanitize=undefined", "-fno-sanitize-recover=all", "-I", include,
                       *c_sources, "-o", exe]
            build_env = os.environ.copy()
            build_env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        run(command, folder, folder / "build.log", build_env)
        output = run([exe, vectors], folder, folder / "run.log", build_env)
        result = json.loads(output)
        assert result == {"scale_checks": coverage["scale_checks"],
                          "floor_checks": coverage["floor_checks"], "mismatches": 0}
        builds[name] = {"passed": True, "result": result, "exe_sha256": sha(exe),
                        "command": [str(x) for x in command]}
    report = {"passed": True, "contract": str(args.contract), "contract_sha256": CONTRACT_HASH,
              "artifact_index_sha256": sha(args.contract / "artifact_hashes.json"),
              "source_hashes": hashes, "fixture_sha256": sha(vectors), "coverage": coverage,
              "builds": builds, "board_executed": False,
              "scope": "wide log multiplication RNE and exact floor predicate; not MFCC acceptance"}
    dump(args.out / "math_report.json", report)
    dump(args.out / "artifact_hashes.json", {p.relative_to(args.out).as_posix(): sha(p)
         for p in sorted(args.out.rglob("*")) if p.is_file()})
    print(json.dumps({"passed": True, "out": str(args.out), **coverage}))


if __name__ == "__main__":
    main()
