"""Read a bounded trace prefix and diagnose only frames with accepted C12 output.

This helper never updates a simulation run. A partial diagnostic cannot establish
full-run protocol or numerical acceptance, even if all expected C12s are present.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np

PROJECT = Path(__file__).resolve().parents[2]
BUILD = PROJECT.parent / "build" / "fp32_hw"
NAMES = {0: "input_float", 10: "preemphasis", 11: "frames", 12: "windowed",
         1: "fft", 2: "power", 3: "mel_energies", 4: "log_mel", 5: "dct", 6: "mfcc"}
FRAME_STAGES = [11, 12, 1, 2, 3, 4, 5, 6]
WIDTHS = {11: 512, 12: 512, 1: 257, 2: 257, 3: 26, 4: 26, 5: 13, 6: 13}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def analyze(run_dir: Path, output: Path) -> dict:
    run_dir, output = Path(run_dir).resolve(), Path(output).resolve()
    build = BUILD.resolve()
    if not output.is_relative_to(build) or output == build:
        raise ValueError("Output must be a new diagnostic directory beneath build/fp32_hw")
    if output == run_dir or output.is_relative_to(run_dir) or run_dir.is_relative_to(output):
        raise ValueError("Diagnostic output must be separate from the simulation run")
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "partial": True,
              "full_run_acceptance_evaluated": False,
              "notice": "Only accepted complete-frame prefixes are diagnosed; this is not an all-pass result.",
              "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "run_dir": str(run_dir), "output": str(output),
              "evaluation_audio_read": False, "input_audio_read": False,
              "physical_board_accessed": False, "cases": [], "protocol_errors": [],
              "first_failure": None, "first_pc_c_difference_outside_tolerance": None}
    try:
        frozen_bytes = (run_dir / "freeze.json").read_bytes()
        freeze = json.loads(frozen_bytes)
        cases = {case["number"]: case for case in freeze["cases"]}
        if len(cases) != len(freeze["cases"]) or not cases:
            raise ValueError("Missing or duplicate frozen case numbers")
        if any(case["group"] not in ("synthetic", "development") for case in cases.values()):
            raise ValueError("Only synthetic/development references are permitted")
        if freeze.get("evaluation_audio_read") is not False:
            raise ValueError("Run does not explicitly exclude evaluation audio")
        result["freeze_sha256"] = digest(frozen_bytes)
        result["frozen_reference_ids"] = {
            "python_root": freeze["python_root"], "c_root": freeze["c_root"],
            "cases": [{key: case[key] for key in ("number", "id", "group", "pcm_sha256")}
                      for case in cases.values()]}
        tolerance_bytes = (run_dir / "tolerances.json").read_bytes()
        if digest(tolerance_bytes) != freeze["tolerance_sha256"]:
            raise ValueError("Frozen tolerances changed")
        tolerances = json.loads(tolerance_bytes)["stages"]
        result["tolerance_sha256"] = digest(tolerance_bytes)
        comparer = run_dir / "source/verification/fp32/compare.py"
        comparer_bytes = comparer.read_bytes()
        comparer_hash = digest(comparer_bytes)
        if comparer_hash != freeze["source_hashes"]["verification/fp32/compare.py"]:
            raise ValueError("Frozen comparer changed")
        helper_bytes = Path(__file__).read_bytes()
        result["helper_sha256"] = digest(helper_bytes)
        result["comparer_sha256"] = comparer_hash
        source = output / "source"
        source.mkdir()
        (source / "analyze_partial.py").write_bytes(helper_bytes)
        (source / "compare.py").write_bytes(comparer_bytes)
        (output / "freeze_snapshot.json").write_bytes(frozen_bytes)
        (output / "tolerances.json").write_bytes(tolerance_bytes)
        # Avoid import-cache writes in the immutable simulation source tree.
        spec = importlib.util.spec_from_file_location("partial_frozen_comparer", source / "compare.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Fix the upper bound before reading. New simulator writes belong to a later
        # diagnostic. A trailing record without a newline is deliberately excluded.
        with (run_dir / "traces.txt").open("rb") as stream:
            snapshot_size = os.fstat(stream.fileno()).st_size
            raw = stream.read(snapshot_size)
        boundary = raw.rfind(b"\n") + 1
        prefix = raw[:boundary]
        (output / "traces_prefix.txt").write_bytes(prefix)
        result["trace_snapshot"] = {"file_size_at_open": snapshot_size,
            "bytes_read": len(raw), "complete_record_bytes": len(prefix),
            "ignored_trailing_bytes": len(raw) - len(prefix), "sha256": digest(prefix),
            "complete_lines": prefix.count(b"\n")}
        counts = {(number, stage): 0 for number in cases for stage in NAMES}
        words_by_stage = {(number, stage): [] for number in cases for stage in FRAME_STAGES}
        completed = {number: 0 for number in cases}
        completion_cycles = {number: [] for number in cases}
        last_cycle = -1
        last_case_position = -1
        case_positions = {number: position for position, number in enumerate(cases)}
        for line_number, line in enumerate(prefix.splitlines(), 1):
            try:
                fields = line.split()
                if len(fields) != 7:
                    raise ValueError("expected seven fields")
                number, stage, frame, index = (int(word) for word in fields[:4])
                if number not in cases or stage not in NAMES:
                    raise ValueError("unknown case or stage")
                if case_positions[number] < last_case_position:
                    raise ValueError("case order moved backwards")
                last_case_position = case_positions[number]
                cycle = int(fields[6])
                if cycle < last_cycle:
                    raise ValueError("cycle count moved backwards")
                last_cycle = cycle
                count = counts[number, stage]
                if stage in WIDTHS:
                    width = WIDTHS[stage]
                    expected = (count // width, count % width)
                    limit = cases[number]["frames"] * width
                else:
                    expected = (0, count)
                    limit = cases[number]["samples"]
                if (frame, index) != expected or count >= limit:
                    raise ValueError(f"order/duplicate/range {(frame, index)} != {expected}, count={count}/{limit}")
                bits = tuple(int(word, 16) for word in fields[4:6])
                if any(word < 0 or word > 0xffffffff for word in bits):
                    raise ValueError("value is not a 32-bit word")
                counts[number, stage] += 1
                if stage in WIDTHS:
                    words_by_stage[number, stage].append(bits)
                if stage == 6 and index == 12:
                    completed[number] += 1
                    completion_cycles[number].append(cycle)
            except (ValueError, KeyError) as error:
                result["protocol_errors"].append({"line": line_number, "reason": str(error)})
                break

        used_reference_hashes = {}

        def reference_bytes(kind: str, path: Path) -> bytes:
            root = Path(freeze["python_root" if kind == "python" else "c_root"]).resolve()
            path = path.resolve()
            relative = path.relative_to(root).as_posix()
            if not relative.startswith(("development/", "synthetic/")):
                raise ValueError("Reference path is outside the permitted development groups")
            key = kind + "/" + relative
            data = path.read_bytes()
            observed = digest(data)
            if observed != freeze["reference_hashes"].get(key):
                raise ValueError(f"Immutable reference hash mismatch: {key}")
            used_reference_hashes[key] = observed
            return data

        for number, case in cases.items():
            nframes = completed[number]
            record = {"id": case["id"], "number": number, "group": case["group"],
                "completed_frames": nframes, "expected_frames": case["frames"],
                "progress": f"{nframes}/{case['frames']}",
                "last_accepted_frame_id": nframes - 1 if nframes else None,
                "last_accepted_c12_cycle": completion_cycles[number][-1] if nframes else None,
                "stages": {}, "completed_prefix_numeric_passed": None,
                "completed_prefix_protocol_passed": True}
            result["cases"].append(record)
            if not nframes:
                continue
            directory = Path(case["python_reference"])
            arrays = json.loads(reference_bytes("python", directory / "arrays.json"))
            if arrays["profile_id"] != "comparison_raw13":
                raise ValueError("Unsupported reference profile")
            actuals = {}
            record["completed_prefix_numeric_passed"] = True
            for stage in FRAME_STAGES:
                name, width = NAMES[stage], WIDTHS[stage]
                needed = nframes * width
                if counts[number, stage] < needed:
                    result["protocol_errors"].append({"case": case["id"], "stage": name,
                        "reason": f"Accepted C12 but stage missing: {counts[number, stage]}/{needed}"})
                    record["completed_prefix_protocol_passed"] = False
                    record["completed_prefix_numeric_passed"] = False
                    continue
                bits = np.asarray(words_by_stage[number, stage][:needed], dtype="<u4")
                floats = bits.view("<f4").astype(np.float64)
                hw = ((floats[:, 0] + 1j * floats[:, 1]) if stage == 1 else floats[:, 0]).reshape(nframes, width)
                actuals[stage] = hw
                if not np.isfinite(hw).all():
                    record["stages"][name] = {"passed": False,
                        "nonfinite": int(np.count_nonzero(~np.isfinite(hw))),
                        "reason": "Nonfinite completed-frame output; numerical metrics omitted"}
                    record["completed_prefix_numeric_passed"] = False
                    record["completed_prefix_protocol_passed"] = False
                    result["protocol_errors"].append({"case": case["id"], "stage": name,
                                                      "reason": "Nonfinite completed-frame output"})
                    continue
                entry = arrays["arrays"][name]
                if entry["shape"] != [case["frames"], width]:
                    raise ValueError(f"Reference shape mismatch: {name}")
                py = np.frombuffer(reference_bytes("python", directory / entry["file"]), dtype=entry["dtype"]).reshape(entry["shape"])[:nframes]
                pc_dtype = "<c8" if stage == 1 else "<f4"
                pc = np.frombuffer(reference_bytes("c", Path(case["c_reference"]) / (name + ".bin")),
                                   dtype=pc_dtype).reshape(entry["shape"])[:nframes].astype(hw.dtype)
                if not np.isfinite(py).all() or not np.isfinite(pc).all():
                    raise ValueError(f"Nonfinite immutable reference: {name}")
                measurement = module.metric(hw, py, tolerances[name])
                measurement["versus_pc_c"] = module.metric(hw, pc, tolerances[name])
                record["stages"][name] = measurement
                if not measurement["passed"]:
                    record["completed_prefix_numeric_passed"] = False
                    if result["first_failure"] is None:
                        result["first_failure"] = {"case": case["id"], "stage": name,
                            "index": measurement["first_violation_index"]}
                if not measurement["versus_pc_c"]["passed"] and result["first_pc_c_difference_outside_tolerance"] is None:
                    result["first_pc_c_difference_outside_tolerance"] = {"case": case["id"], "stage": name,
                        "index": measurement["versus_pc_c"]["first_violation_index"]}
            if 5 in actuals and 6 in actuals and not np.array_equal(
                    actuals[5].astype("<f4").view("<u4"), actuals[6].astype("<f4").view("<u4")):
                record["completed_prefix_protocol_passed"] = False
                result["protocol_errors"].append({"case": case["id"], "reason": "DCT/MFCC bits differ"})
        result["verified_reference_hashes"] = used_reference_hashes
        result["completed_frames"] = sum(completed.values())
        result["expected_frames"] = sum(case["frames"] for case in cases.values())
        result["progress"] = f"{result['completed_frames']}/{result['expected_frames']}"
        result["completed_prefix_protocol_passed"] = not result["protocol_errors"]
        result["status"] = ("partial_protocol_failure" if result["protocol_errors"] else
            "partial_numerical_failure" if result["first_failure"] else
            "partial_pc_c_difference_outside_tolerance" if result["first_pc_c_difference_outside_tolerance"] else
            "partial_no_violations_observed" if result["completed_frames"] else "partial_no_completed_frames")
        return result
    except Exception as error:
        result["status"] = "diagnostic_error"
        result["error"] = str(error)
        raise
    finally:
        result["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(output / "partial_comparison.json", result)
        write_json(output / "artifact_manifest.json", {
            path.relative_to(output).as_posix(): digest(path.read_bytes())
            for path in sorted(output.rglob("*")) if path.is_file() and path.name != "artifact_manifest.json"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.run_dir, args.output)
    print(json.dumps({key: result[key] for key in ("partial", "status", "progress", "first_failure", "output")}))
    return 1 if (result["protocol_errors"] or result["first_failure"] or
                 result["first_pc_c_difference_outside_tolerance"]) else 0


if __name__ == "__main__":
    sys.exit(main())
