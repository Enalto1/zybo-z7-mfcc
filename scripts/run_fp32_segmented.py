"""Freeze and optionally execute eight independent copies of a validated xsim snapshot.

Preparation is the default. --execute is required for the complete development
replay; --launch-probe runs only one 512-sample relocation check. No evaluation
audio is read, and no complete-clip protocol/throughput claim is made.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT.parent
XSIM = Path("C:/Xilinx/Vivado/2024.2/bin/xsim.bat")
PY_INDEX_SHA = "dbd2678abf8273642396edfa153eb642636e26bcdc69f9db40f06839deddb243"
C_INDEX_SHA = "e4abda3effe428e36f055965179d33e75e86c3c751753625952c63f6b3c41d85"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_module(path):
    spec = importlib.util.spec_from_file_location("frozen_segment_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_hashes(directory):
    return {path.relative_to(directory).as_posix(): sha(path)
            for path in sorted(directory.rglob("*")) if path.is_file()}


def verify_hashes(directory, expected):
    for relative, value in expected.items():
        if sha(directory / relative) != value:
            raise ValueError(f"Frozen input changed: {directory / relative}")


def copy_runtime(source, destination, hashes):
    destination.mkdir(parents=True, exist_ok=False)
    for relative, expected in hashes.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        if sha(target) != expected:
            raise ValueError(f"Compiled snapshot copy mismatch: {relative}")


def simulator_command(directory):
    return [str(directory / "launch.bat")]


def write_case(directory, pcm, number):
    (directory / "input_s16le.pcm").write_bytes(pcm)
    words = np.frombuffer(pcm, dtype="<u2")
    text = "".join(f"{int(word):04x}\n" for word in words)
    memory = directory / "input.mem"
    memory.write_text(text, encoding="ascii", newline="\n")
    decoded = np.asarray([int(word, 16) for word in text.splitlines()], dtype="<u2").tobytes()
    if decoded != pcm:
        raise ValueError("PCM-to-hex roundtrip changed a bit")
    (directory / "cases.txt").write_text(f"{memory.as_posix()} {len(words)} {number}\n", encoding="ascii")
    (directory / "run.tcl").write_text("run all\nquit\n", encoding="ascii")
    # Xilinx's Windows batch argument parser splits unquoted '=' arguments.
    # Quote every fixed/generated argument inside this frozen single-call file.
    arguments = [str(XSIM), "tb_fp32_mfcc_behav", "-tclbatch", (directory / "run.tcl").as_posix(),
                 "-log", (directory / "xsim.log").as_posix(), "-testplusarg",
                 "CASES=" + (directory / "cases.txt").as_posix(), "-testplusarg", "OUTPUT=" + directory.as_posix()]
    if any(any(character in argument for character in '\"%\r\n') for argument in arguments):
        raise ValueError("Unsupported character in the controlled Windows launch paths")
    (directory / "launch.bat").write_text('@echo off\ncall ' + " ".join('"' + a + '"' for a in arguments) +
                                           '\nexit /b %errorlevel%\n', encoding="ascii")


def launch(directory, command):
    started = time.perf_counter()
    with (directory / "console.log").open("w", encoding="utf-8") as output:
        completed = subprocess.run(command, cwd=directory, stdout=output, stderr=subprocess.STDOUT, check=False)
    record = {"directory": str(directory), "command": command, "exit_code": completed.returncode,
              "host_wall_seconds": time.perf_counter() - started}
    dump(directory / "process.json", record)
    return record


def prepare(out, snapshot_run, workers):
    snapshot_run = snapshot_run.resolve()
    snapshot_index_path = snapshot_run / "artifact_manifest.json"
    snapshot_index = json.loads(snapshot_index_path.read_text())
    for relative in ("run_manifest.json", "freeze.json", "coefficients/coefficient_manifest.json"):
        if sha(snapshot_run / relative) != snapshot_index.get(relative):
            raise ValueError(f"Completed smoke artifact index mismatch: {relative}")
    snapshot_result = json.loads((snapshot_run / "run_manifest.json").read_text())
    if snapshot_result.get("status") != "passed" or not snapshot_result.get("numeric_acceptance_passed"):
        raise ValueError("A completed numerically/protocol-passed snapshot run is required")
    original_freeze = json.loads((snapshot_run / "freeze.json").read_text())
    verify_hashes(snapshot_run / "source", original_freeze["source_hashes"])
    coefficient_manifest = json.loads((snapshot_run / "coefficients/coefficient_manifest.json").read_text())
    for table in coefficient_manifest["tables"].values():
        if sha(snapshot_run / "coefficients" / table["file"]) != table["sha256"]:
            raise ValueError("Snapshot coefficient ROM identity changed")
    ip_run = Path(original_freeze["ip_run"])
    if sha(ip_run / "ip_manifest.json") != original_freeze["ip_manifest_sha256"]:
        raise ValueError("IP manifest identity changed")
    runtime = snapshot_run / "project/fp32_mfcc.sim/sim_1/behav/xsim"
    if not (runtime / "xsim.dir/tb_fp32_mfcc_behav/xsimk.exe").is_file():
        raise ValueError("Compiled simulator snapshot is absent")
    runtime_hashes = {}
    for path in sorted((runtime / "xsim.dir").rglob("*")):
        if path.is_file() and path.suffix.lower() not in (".log", ".jou", ".wdb") and path.name != "TempBreakPointFile.txt":
            runtime_hashes[path.relative_to(runtime).as_posix()] = sha(path)
    runtime_hashes["xsim.ini"] = sha(runtime / "xsim.ini")
    for table in coefficient_manifest["tables"].values():
        name = table["file"]
        if sha(runtime / name) != table["sha256"]:
            raise ValueError("Compiled runtime ROM differs from frozen coefficient file")
        runtime_hashes[name] = table["sha256"]
    runtime_relative = runtime.relative_to(snapshot_run).as_posix()
    for relative, value in runtime_hashes.items():
        if snapshot_index.get(runtime_relative + "/" + relative) != value:
            raise ValueError(f"Compiled input differs from completed smoke artifact index: {relative}")

    pyroot, croot = Path(original_freeze["python_root"]), Path(original_freeze["c_root"])
    if sha(pyroot / "artifact_manifest.json") != PY_INDEX_SHA or sha(croot / "artifact_manifest.json") != C_INDEX_SHA:
        raise ValueError("Immutable Python/C baseline identity changed")
    pyindex = json.loads((pyroot / "artifact_manifest.json").read_text())
    cindex = json.loads((croot / "artifact_manifest.json").read_text())
    case_id = "8463-294828-0037"
    pycase = pyroot / "development" / case_id
    ccase = croot / "development" / case_id
    pyarrays = pycase / "comparison_raw13"
    reference_hashes = {}
    reference_paths = {"python": {}, "c": {}}
    for kind, root, index, files in (
        ("python", pyroot, pyindex, [pycase / "input_s16le.pcm", pyarrays / "arrays.json"] +
         [pyarrays / (name + ".bin") for name in load_module(PROJECT / "verification/fp32/segment_compare.py").STAGES.values()]),
        ("c", croot, cindex, [ccase / (name + ".bin") for name in load_module(PROJECT / "verification/fp32/segment_compare.py").STAGES.values()])):
        for path in files:
            relative = path.relative_to(root).as_posix()
            value = sha(path)
            if index.get(relative) != value:
                raise ValueError(f"Original reference changed: {path}")
            reference_hashes[kind + "/" + relative] = value
            reference_paths[kind][relative] = value
    pcm = (pycase / "input_s16le.pcm").read_bytes()
    if len(pcm) != 85920 * 2:
        raise ValueError("Unexpected frozen development sample count")
    module = load_module(PROJECT / "verification/fp32/segment_compare.py")
    mapping_test = module.self_test()
    segments = module.plan_segments(len(pcm) // 2, workers)
    source_hashes = {}
    for relative in ("scripts/run_fp32_segmented.py", "verification/fp32/segment_compare.py"):
        source = PROJECT / relative
        target = out / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        source_hashes[relative] = sha(target)
    for relative in original_freeze["source_hashes"]:
        target = out / "compiled_source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot_run / "source" / relative, target)
    comparer = out / "source/verification/fp32/compare.py"
    shutil.copyfile(snapshot_run / "source/verification/fp32/compare.py", comparer)
    source_hashes["verification/fp32/compare.py"] = sha(comparer)
    shutil.copyfile(snapshot_run / "tolerances.json", out / "tolerances.json")
    if sha(out / "tolerances.json") != original_freeze["tolerance_sha256"]:
        raise ValueError("Tolerance identity changed")
    reconstructed = bytearray()
    for segment in segments:
        directory = out / "workers" / f"segment_{segment['number']:02d}"
        copy_runtime(runtime, directory, runtime_hashes)
        selected = pcm[segment["origin_sample"] * 2:segment["end_sample"] * 2]
        write_case(directory, selected, segment["number"])
        offset_a = (segment["owned_sample_first"] - segment["origin_sample"]) * 2
        offset_b = (segment["owned_sample_stop"] - segment["origin_sample"]) * 2
        reconstructed.extend(selected[offset_a:offset_b])
        segment["pcm_slice_sha256"] = sha(directory / "input_s16le.pcm")
        segment["hex_sha256"] = sha(directory / "input.mem")
        segment["hex_roundtrip_bit_mismatches"] = 0
        segment["command"] = simulator_command(directory)
        segment["prepared_input_hashes"] = {name: sha(directory / name)
            for name in ("input_s16le.pcm", "input.mem", "cases.txt", "run.tcl", "launch.bat")}
    if bytes(reconstructed) != pcm:
        raise ValueError("Unique sample ownership did not reconstruct the original complete PCM")
    freeze = {"schema_version": 1, "segmented_replay": True, "status": "frozen_before_execution",
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "workers": workers,
        "python_version": sys.version, "numpy_version": np.__version__, "snapshot_run": str(snapshot_run),
        "snapshot_run_manifest_sha256": sha(snapshot_run / "run_manifest.json"),
        "snapshot_artifact_index_sha256": sha(snapshot_index_path),
        "snapshot_freeze_sha256": sha(snapshot_run / "freeze.json"), "source_hashes": source_hashes,
        "compiled_source_hashes": original_freeze["source_hashes"], "runtime_source": str(runtime),
        "runtime_hashes": runtime_hashes, "ip_manifest_sha256": original_freeze["ip_manifest_sha256"],
        "coefficient_manifest_sha256": sha(snapshot_run / "coefficients/coefficient_manifest.json"),
        "tolerance_sha256": original_freeze["tolerance_sha256"], "xsim_launcher": str(XSIM),
        "xsim_launcher_sha256": sha(XSIM), "python_root": str(pyroot), "c_root": str(croot),
        "python_artifact_index_sha256": PY_INDEX_SHA, "c_artifact_index_sha256": C_INDEX_SHA,
        "reference_hashes": reference_hashes, "reference_paths": reference_paths,
        "case": {"id": case_id, "samples": len(pcm)//2, "frames": 534,
                 "python_reference": str(pyarrays), "c_reference": str(ccase),
                 "pcm_sha256": hashlib.sha256(pcm).hexdigest()}, "segments": segments,
        "pcm_ownership_reconstruction_bit_mismatches": 0, "mapper_self_test": mapping_test,
        "evaluation_audio_read": False, "physical_board_accessed": False,
        "claim_limit": "If all workers and mapping checks complete, numerical coverage is all original frames via independent overlapping vendor simulations; preparation alone establishes no numerical result and this cannot establish uninterrupted full-clip protocol or throughput."}
    dump(out / "freeze.json", freeze)
    dump(out / "run_manifest.json", freeze)
    return freeze


def verify_prepared(out, freeze):
    verify_hashes(out / "source", freeze["source_hashes"])
    verify_hashes(out / "compiled_source", freeze["compiled_source_hashes"])
    if sha(out / "tolerances.json") != freeze["tolerance_sha256"]:
        raise ValueError("Frozen tolerance changed")
    for kind, root_name in (("python", "python_root"), ("c", "c_root")):
        verify_hashes(Path(freeze[root_name]), freeze["reference_paths"][kind])
    for segment in freeze["segments"]:
        directory = out / "workers" / f"segment_{segment['number']:02d}"
        verify_hashes(directory, freeze["runtime_hashes"])
        verify_hashes(directory, segment["prepared_input_hashes"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--snapshot-run", type=Path, default=WORK / "build/fp32_hw/integration_compact_smoke_01")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--launch-probe", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(load_module(PROJECT / "verification/fp32/segment_compare.py").self_test(), indent=2))
        return 0
    if not args.run_id or not args.run_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("A fresh simple --run-id is required")
    if args.execute and args.launch_probe:
        raise ValueError("Choose full execution or relocation probe, not both")
    out = WORK / "build/fp32_hw" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "preparing", "segmented_replay": True}
    try:
        manifest = prepare(out, args.snapshot_run, args.workers)
        verify_prepared(out, manifest)
        if args.launch_probe:
            probe = out / "relocation_probe"
            copy_runtime(Path(manifest["runtime_source"]), probe, manifest["runtime_hashes"])
            pcm = Path(manifest["python_root"], "development", manifest["case"]["id"], "input_s16le.pcm").read_bytes()[:1024]
            write_case(probe, pcm, 0)
            probe_record = {"number": 0, "samples": 512, "frames": 1}
            probe_command = simulator_command(probe)
            probe_inputs = {name: sha(probe / name) for name in
                            ("input_s16le.pcm", "input.mem", "cases.txt", "run.tcl", "launch.bat")}
            dump(probe / "probe_freeze.json", {"scope": "one initial development frame; no full replay",
                "command": probe_command, "pcm_sha256": sha(probe / "input_s16le.pcm"),
                "hex_sha256": sha(probe / "input.mem"), "runtime_hashes": manifest["runtime_hashes"],
                "prepared_input_hashes": probe_inputs,
                "parent_freeze_sha256": sha(out / "freeze.json")})
            process = launch(probe, probe_command)
            if process["exit_code"]:
                raise RuntimeError("Relocated xsim probe failed")
            module = load_module(out / "source/verification/fp32/segment_compare.py")
            raw, protocol = module.validate_worker(probe, probe_record)
            # The probe starts at original sample zero, so every raw stage maps
            # directly to the first original frame / first512 samples.
            comparer = load_module(out / "source/verification/fp32/compare.py")
            index = json.loads(Path(manifest["case"]["python_reference"], "arrays.json").read_text())["arrays"]
            tolerances = json.loads((out / "tolerances.json").read_text())["stages"]
            numerical = {}
            for stage in module.ORDER:
                name = module.STAGES[stage]
                dtype = "<c8" if stage == 1 else "<f4"
                bits = raw[stage]
                actual = bits.view(dtype).reshape(bits.shape[:-1]) if stage == 1 else bits.view(dtype)
                actual = actual.astype(np.complex128 if stage == 1 else np.float64)
                entry = index[name]
                reference = np.fromfile(Path(manifest["case"]["python_reference"]) / entry["file"],
                                        dtype=entry["dtype"]).reshape(entry["shape"])
                expected = reference[:1] if stage in module.WIDTH else reference[:512]
                numerical[name] = comparer.metric(actual, expected, tolerances[name])
            if not all(item["passed"] for item in numerical.values()):
                dump(probe / "numerical.json", numerical)
                raise RuntimeError("Relocated actual IP probe numeric stage failure")
            dump(probe / "numerical.json", numerical)
            verify_prepared(out, manifest)
            verify_hashes(probe, manifest["runtime_hashes"])
            verify_hashes(probe, probe_inputs)
            manifest["relocation_probe"] = {"process": process, "protocol": protocol,
                                            "numerical_passed": True,
                                            "full_development_execution_started": False}
            manifest["status"] = "prepared_and_relocation_probe_passed"
        elif args.execute:
            manifest["processes"] = []
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                jobs = {executor.submit(launch, out / "workers" / f"segment_{s['number']:02d}", s["command"]): s
                        for s in manifest["segments"]}
                for job in as_completed(jobs):
                    process = job.result()
                    process["segment"] = jobs[job]["number"]
                    manifest["processes"].append(process)
                    dump(out / "run_manifest.json", manifest)
                    print(json.dumps({"worker_completed": process["segment"], "exit_code": process["exit_code"]}), flush=True)
            if any(p["exit_code"] for p in manifest["processes"]):
                raise RuntimeError("One or more actual vendor workers failed")
            # Runtime logs may change; compiled code, ROMs, inputs and references must not.
            verify_prepared(out, manifest)
            module = load_module(out / "source/verification/fp32/segment_compare.py")
            comparison = module.compare_segmented(out)
            manifest["numeric_acceptance_passed"] = comparison["numeric_acceptance_passed"]
            manifest["protocol_passed"] = comparison["protocol_passed"]
            manifest["protocol_scope"] = comparison["protocol_scope"]
            manifest["single_clip_protocol_passed"] = None
            manifest["unique_owned_frames"] = comparison["mapping"]["unique_owned_frames"]
            manifest["status"] = "completed_segmented_passed" if comparison["numeric_acceptance_passed"] else "completed_segmented_numerical_failures"
        else:
            manifest["status"] = "prepared_not_executed"
        return 2 if manifest["status"] == "completed_segmented_numerical_failures" else 0
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        raise
    finally:
        manifest["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        dump(out / "run_manifest.json", manifest)
        dump(out / "artifact_manifest.json", {k: v for k, v in file_hashes(out).items() if k != "artifact_manifest.json"})
        print(json.dumps({"run": str(out), "status": manifest["status"]}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
