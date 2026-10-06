"""Compare saved stage bits from completed, artifact-bound raw13 IP runs."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

import numpy as np


STAGES = ("input_float", "preemphasis", "frames", "windowed", "fft", "power",
          "mel_energies", "log_mel", "dct", "mfcc")
COMPLETED = {"passed", "completed_with_numerical_failures"}
PROFILE = "comparison_raw13"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"Path escapes its recorded root: {relative}")
    return path


def artifact(root, index, relative):
    path = contained(root, relative)
    digest = sha(path)
    if index.get(relative) != digest:
        raise ValueError(f"Missing or changed artifact binding: {root / relative}")
    return path, digest


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def frozen_reference(freeze, path):
    root = Path(freeze["python_root"]).resolve()
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Python reference escapes its frozen root: {path}")
    key = "python/" + path.relative_to(root).as_posix()
    digest = sha(path)
    if freeze["reference_hashes"].get(key) != digest:
        raise ValueError(f"Missing or changed frozen Python reference: {path}")
    return digest


def inspect_run(root):
    index_path = root / "artifact_manifest.json"
    index = read_json(index_path)
    run_path, run_hash = artifact(root, index, "run_manifest.json")
    freeze_path, freeze_hash = artifact(root, index, "freeze.json")
    run, freeze = read_json(run_path), read_json(freeze_path)
    if run.get("status") not in COMPLETED:
        raise ValueError(f"Run is not completed: {root}: {run.get('status')}")
    if freeze.get("status") != "frozen_before_execution":
        raise ValueError(f"Missing pre-execution freeze: {root}")
    for key in ("cases", "source_hashes", "reference_hashes", "python_root", "c_root"):
        if key not in freeze or run.get(key) != freeze[key]:
            raise ValueError(f"Run/freeze disagreement for {key}: {root}")
    if not freeze["source_hashes"] or not freeze["cases"]:
        raise ValueError(f"Empty sources or cases: {root}")
    for relative, digest in freeze["source_hashes"].items():
        _, saved_hash = artifact(root, index, "source/" + relative)
        if saved_hash != digest:
            raise ValueError(f"Source snapshot differs from freeze: {relative}")

    coeff_path, coeff_hash = artifact(root, index, "coefficients/coefficient_manifest.json")
    coeff = read_json(coeff_path)
    if coeff.get("profile_id") != PROFILE:
        raise ValueError(f"Coefficient profile must be {PROFILE}: {root}")
    for entry in coeff["tables"].values():
        _, digest = artifact(root, index, "coefficients/" + entry["file"])
        if digest != entry["sha256"]:
            raise ValueError(f"Coefficient table differs from its manifest: {entry['file']}")
    coefficient_identity = {
        "profile_id": coeff["profile_id"],
        "source_coefficient_hashes": {name: coeff["tables"][name]["source_sha256"]
                                      for name in ("window", "mel", "dct_cosine", "dct_scale")},
        "constants": coeff["constants"],
        "power_scale_bits_hex": coeff["power_scale_bits_hex"],
    }

    cases = []
    schemas = {}
    expected_files = set()
    seen_ids = set()
    for case in freeze["cases"]:
        case_id = case["id"]
        if not isinstance(case_id, str) or Path(case_id).name != case_id or case_id in seen_ids:
            raise ValueError(f"Invalid or duplicate case id: {case_id}")
        seen_ids.add(case_id)
        samples, frames = case["samples"], case["frames"]
        if type(samples) is not int or samples < 0 or type(frames) is not int:
            raise ValueError(f"Invalid sample/frame counts: {case_id}")
        if frames != (0 if samples < 512 else 1 + (samples - 512) // 160):
            raise ValueError(f"Frame count violates raw13 full-frame policy: {case_id}")
        reference_dir = Path(case["python_reference"]).resolve()
        schema_path = reference_dir / "arrays.json"
        schema_hash = frozen_reference(freeze, schema_path)
        reference = read_json(schema_path)
        if reference.get("profile_id") != PROFILE:
            raise ValueError(f"Python schema profile must be {PROFILE}: {case_id}")
        pcm_path = reference_dir.parent / "input_s16le.pcm"
        pcm_hash = frozen_reference(freeze, pcm_path)
        if pcm_hash != case["pcm_sha256"] or pcm_path.stat().st_size != samples * 2:
            raise ValueError(f"PCM identity or sample count differs: {case_id}")
        _, hex_hash = artifact(root, index, f"inputs/{case_id}.mem")
        if hex_hash != case["hex_sha256"] or case.get("input_transport_bit_mismatches") != 0:
            raise ValueError(f"Frozen input transport was not verified: {case_id}")
        shape_contract = {
            "input_float": [samples], "preemphasis": [samples],
            "frames": [frames, 512], "windowed": [frames, 512],
            "fft": [frames, 257], "power": [frames, 257],
            "mel_energies": [frames, 26], "log_mel": [frames, 26],
            "dct": [frames, 13], "mfcc": [frames, 13],
        }
        actual_schema_path = root / f"arrays/{case_id}/arrays.json"
        actual_schema = None
        actual_schema_hash = None
        if actual_schema_path.exists() or f"arrays/{case_id}/arrays.json" in index:
            _, actual_schema_hash = artifact(root, index, f"arrays/{case_id}/arrays.json")
            actual_schema = read_json(actual_schema_path)
            if set(actual_schema["arrays"]) != set(STAGES):
                raise ValueError(f"Saved actual schema has a different stage set: {case_id}")
        case_schema = {}
        for stage in STAGES:
            entry = reference["arrays"][stage]
            source_dtype = "<c16" if stage == "fft" else "<f8"
            dtype = "<c8" if stage == "fft" else "<f4"
            shape = entry["shape"]
            if entry["dtype"] != source_dtype or shape != shape_contract[stage]:
                raise ValueError(f"Unexpected canonical reference schema: {case_id}/{stage}")
            relative = f"arrays/{case_id}/{stage}.bin"
            path, digest = artifact(root, index, relative)
            byte_count = math.prod(shape) * np.dtype(dtype).itemsize
            if path.stat().st_size != byte_count:
                raise ValueError(f"Exact byte count differs (including any trailing bytes): {relative}")
            if actual_schema is not None:
                saved = actual_schema["arrays"][stage]
                if (saved["file"] != stage + ".bin" or saved["dtype"] != dtype
                        or saved["shape"] != shape or saved["sha256"] != digest
                        or ("bytes" in saved and saved["bytes"] != byte_count)):
                    raise ValueError(f"Actual schema differs from canonical schema/artifact: {relative}")
            expected_files.add(relative)
            schemas[relative] = {"stage": stage, "case_id": case_id, "shape": shape,
                                 "dtype": dtype, "bytes": byte_count,
                                 "reference_dtype": source_dtype,
                                 "complex_layout": "interleaved binary32 real,imag" if stage == "fft" else None}
            case_schema[stage] = schemas[relative]
        cases.append({key: case[key] for key in ("number", "id", "group", "samples", "frames",
                                                "pcm_sha256", "hex_sha256")})
        cases[-1].update({"pcm_path": str(pcm_path), "python_schema_path": str(schema_path),
                          "python_schema_sha256": schema_hash, "actual_schema_sha256": actual_schema_hash,
                          "actual_schema_source": "artifact-bound arrays.json" if actual_schema is not None
                          else "inferred binary32 transport from frozen Python reference (legacy run)",
                          "schema": case_schema})
    actual_files = {path.relative_to(root).as_posix() for path in (root / "arrays").rglob("*.bin")}
    if actual_files != expected_files:
        raise ValueError(f"Exact ten-stage-per-case file set differs: {root}; "
                         f"missing={sorted(expected_files - actual_files)}, extra={sorted(actual_files - expected_files)}")
    return {"root": str(root), "status": run["status"], "artifact_manifest_sha256": sha(index_path),
            "run_manifest_sha256": run_hash, "freeze_sha256": freeze_hash,
            "source_hashes": freeze["source_hashes"], "coefficient_manifest_sha256": coeff_hash,
            "coefficient_identity": coefficient_identity, "cases": cases, "schemas": schemas}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = [args.before.resolve(), args.after.resolve()]
    out = args.output.resolve()
    build = (Path(__file__).resolve().parents[3] / "build/fp32_hw").resolve()
    if not out.is_relative_to(build) or out == build or any(out.is_relative_to(p) or p.is_relative_to(out) for p in roots):
        raise ValueError("Use a separate new directory beneath build/fp32_hw")
    provenance = [inspect_run(root) for root in roots]
    identity_keys = ("number", "id", "group", "samples", "frames", "pcm_sha256", "hex_sha256")
    identities = [[{key: case[key] for key in identity_keys} for case in run["cases"]] for run in provenance]
    if identities[0] != identities[1]:
        raise ValueError("Case/input identities or order differ between completed runs")
    if provenance[0]["coefficient_identity"] != provenance[1]["coefficient_identity"]:
        raise ValueError("Underlying raw13 coefficient bits/constants differ between runs")
    if provenance[0]["schemas"] != provenance[1]["schemas"]:
        raise ValueError("Canonical stage schemas differ between completed runs")
    result = {"before": str(roots[0]), "after": str(roots[1]), "files": [],
              "comparison_provenance_passed": True, "provenance": provenance,
              "all_stage_bits_identical": True,
              "scope": "Recorded numerical array bits only; not numerical acceptance, cycle, or whole-design equivalence"}
    for relative, schema in sorted(provenance[0]["schemas"].items()):
        paths = [root / relative for root in roots]
        a, b = [np.fromfile(path, dtype="<u4") for path in paths]
        mismatches = int(np.count_nonzero(a != b))
        result["files"].append({"file": relative, "schema": schema,
                                "binary32_component_words": int(a.size),
                                "before_sha256": sha(paths[0]), "after_sha256": sha(paths[1]),
                                "word_bit_mismatches": mismatches})
        result["all_stage_bits_identical"] &= mismatches == 0
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, out / "compare_revisions_used.py")
    (out / "equivalence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (out / "artifact_manifest.json").write_text(json.dumps({p.name: sha(p) for p in out.iterdir() if p.is_file()}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "files": len(result["files"]),
                      "comparison_provenance_passed": True,
                      "all_stage_bits_identical": result["all_stage_bits_identical"]}))
    return 0 if result["all_stage_bits_identical"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
