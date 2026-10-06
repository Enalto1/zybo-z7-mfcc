"""Freeze sources, generate ROMs, run actual Vivado IP simulation and compare stages.

Only synthetic17 and development1 are eligible. Evaluation audio is never selected.
New run directories are mandatory; unsuccessful probes remain available.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import importlib.util
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT.parent
sys.path.insert(0, str(PROJECT / "verification" / "fp32"))
from generate_coefficients import generate

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ip-run", type=Path, default=WORK / "build/fp32_hw/ip_04")
    parser.add_argument("--selection", choices=("smoke", "synthetic", "development", "all"), default="all")
    parser.add_argument("--mode", choices=("sim", "synth", "all"), default="sim")
    parser.add_argument("--reset-stress", action="store_true", help="Also replay a fixed 512-sample prefix after five abort/reset phases")
    args = parser.parse_args()
    if not args.run_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Simple run identifier required")
    out = WORK / "build/fp32_hw" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    pyroot = WORK / "build/python_reference/reproduce_01"
    croot = WORK / "build/c_reference/reproduce_01"
    pyindex = json.loads((pyroot / "artifact_manifest.json").read_text())
    cindex = json.loads((croot / "artifact_manifest.json").read_text())
    if sha(pyroot / "artifact_manifest.json") != "dbd2678abf8273642396edfa153eb642636e26bcdc69f9db40f06839deddb243":
        raise ValueError("Python baseline identity changed")
    if sha(croot / "artifact_manifest.json") != "e4abda3effe428e36f055965179d33e75e86c3c751753625952c63f6b3c41d85":
        raise ValueError("C baseline identity changed")
    manifest = {"status": "preparing", "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "part": "xc7z020clg400-1", "clock_constraint_ns": 10.0,
        "python_version":sys.version,"python_executable":sys.executable,
        "packages":{name:importlib.metadata.version(name) for name in ("numpy","matplotlib")},
        "python_root": str(pyroot), "c_root": str(croot), "ip_run": str(args.ip_run.resolve()),
        "selection": args.selection, "mode": args.mode, "reset_stress": args.reset_stress, "evaluation_audio_read": False,
        "physical_board_accessed": False, "source_hashes": {}, "reference_hashes": {}, "cases": []}
    sources = list((PROJECT / "hardware/fp32/rtl").glob("*.sv"))
    sources += [PROJECT / "verification/fp32/tb_fp32_mfcc.sv", PROJECT / "verification/fp32/generate_coefficients.py",
        PROJECT / "verification/fp32/compare.py", Path(__file__), PROJECT / "scripts/fp32_integrate.tcl"]
    sources += list((PROJECT / "verification/fp32/reset").glob("*.svh"))
    for source in sources:
        relative = source.relative_to(PROJECT)
        dest = out / "source" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        manifest["source_hashes"][relative.as_posix()] = sha(dest)
    # Freeze existing C/Python float32 acceptance budgets before any hardware observation.
    shutil.copyfile(PROJECT / "verification/c/tolerances.json", out / "tolerances.json")
    manifest["tolerance_sha256"] = sha(out / "tolerances.json")
    if manifest["tolerance_sha256"] != "64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6":
        raise ValueError("Frozen numeric acceptance budgets changed")
    generate(croot, out / "coefficients")
    ipmanifest = args.ip_run / "ip_manifest.json"
    manifest["ip_manifest_sha256"] = sha(ipmanifest)
    ipmeta = json.loads(ipmanifest.read_text(encoding="utf-8-sig"))
    for relative, expected in ipmeta["output_hashes"].items():
        if sha(args.ip_run / relative) != expected:
            raise ValueError(f"IP changed: {relative}")
    candidates = []
    if args.selection in ("smoke", "synthetic", "all"):
        candidates += [("synthetic", path) for path in sorted((pyroot / "synthetic").iterdir()) if path.is_dir()]
    if args.selection in ("development", "all"):
        candidates += [("development", pyroot / "development/8463-294828-0037")]
    if args.selection == "smoke":
        candidates = [(group, path) for group, path in candidates if path.name == "impulse_n0"]
        if not candidates:
            candidates = [("synthetic", next(p for p in sorted((pyroot / "synthetic").iterdir()) if "impulse" in p.name))]
    (out / "inputs").mkdir()
    lines = []
    for number, (group, path) in enumerate(candidates):
        pcm = path / "input_s16le.pcm"
        reference = path / "comparison_raw13"
        files = [pcm, reference / "arrays.json"] + list(reference.glob("*.bin"))
        for file in files:
            relative = file.relative_to(pyroot).as_posix()
            actual = sha(file)
            if pyindex.get(relative) != actual:
                raise ValueError(f"Python artifact changed: {relative}")
            manifest["reference_hashes"]["python/"+relative] = actual
        cpath = croot / group / path.name
        for file in cpath.glob("*.bin"):
            relative = file.relative_to(croot).as_posix()
            actual = sha(file)
            if cindex.get(relative) != actual:
                raise ValueError(f"C artifact changed: {relative}")
            manifest["reference_hashes"]["c/"+relative] = actual
        words = np.frombuffer(pcm.read_bytes(), dtype="<u2")
        hexpath = out / "inputs" / (path.name + ".mem")
        hexpath.write_text("".join(f"{int(word):04x}\n" for word in words), encoding="ascii")
        rebuilt = np.array([int(x, 16) for x in hexpath.read_text().splitlines()], dtype="<u2").tobytes()
        if rebuilt != pcm.read_bytes():
            raise ValueError("PCM transport conversion changed bytes")
        count = len(words)
        record = {"number": number, "id": path.name, "group": group, "samples": count,
                  "frames": max(0, 1+(count-512)//160), "pcm_sha256": sha(pcm),
                  "hex_sha256": sha(hexpath), "input_transport_bit_mismatches": 0,
                  "python_reference": str(reference), "c_reference": str(cpath)}
        manifest["cases"].append(record)
        lines.append(f"{hexpath.as_posix()} {count} {number}\n")
    (out / "cases.txt").write_text("".join(lines), encoding="ascii")
    if args.reset_stress:
        reset_case = next(c for c in manifest["cases"] if c["samples"] >= 512)
        reset_input = out / "inputs" / (reset_case["id"] + ".mem")
        (out / "reset_stress_input.txt").write_text(reset_input.as_posix(), encoding="ascii")
        manifest["reset_stress_input_case"] = reset_case["id"]
    manifest["status"] = "frozen_before_execution"
    write_json(out / "freeze.json", manifest)
    write_json(out / "run_manifest.json", manifest)
    vivado = Path("C:/Xilinx/Vivado/2024.2/bin/vivado.bat")
    command = [str(vivado), "-mode", "batch", "-nojournal", "-log", str(out/"vivado.log"),
               "-source", str(out/"source/scripts/fp32_integrate.tcl"), "-tclargs", str(out), str(args.ip_run), args.mode]
    manifest["command"] = command
    try:
        with (out / "console.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, check=False)
        manifest["vivado_exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"Vivado failed ({result.returncode}); see {out / 'console.log'}")
        if args.mode in ("sim", "all"):
            summary = (out/"simulation_summary.txt").read_text()
            if f"ALL_PROTOCOL_PASS cases={len(candidates)}" not in summary:
                raise RuntimeError("Simulation did not complete all protocol checks")
            if args.reset_stress:
                manifest["reset_summary"] = (out/"reset_summary.txt").read_text()
            spec = importlib.util.spec_from_file_location("fp32_frozen_compare", out/"source/verification/fp32/compare.py")
            compare_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(compare_module)
            comparison = compare_module.compare_run(out)
            manifest["protocol_passed"] = comparison["protocol_passed"]
            manifest["numeric_acceptance_passed"] = comparison["numeric_acceptance_passed"]
            manifest["status"] = "passed" if comparison["numeric_acceptance_passed"] else "completed_with_numerical_failures"
        else:
            manifest["status"] = "synthesized_and_routed_ooc"
        manifest["synthesis_routed_ooc"] = args.mode in ("synth", "all")
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        raise
    finally:
        manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(out/"run_manifest.json", manifest)
        write_json(out/"artifact_manifest.json", {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*"))
                   if p.is_file() and p.name != "artifact_manifest.json" and ".Xil" not in p.parts})
    print(json.dumps({"run": str(out), "status": manifest["status"]}))
    return 0 if manifest["status"] != "completed_with_numerical_failures" else 2

if __name__ == "__main__":
    raise SystemExit(main())
