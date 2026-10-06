"""Reproduce a gated development -> frozen evaluation MFCC experiment.

Outputs are data and numerical evidence, not ARM/FPGA or recognition benchmarks.
The input dataset manifest and installed comparison package are never modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import traceback
import wave

# Set before importing numerical libraries. Record this in every run.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"
sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
BUILD = WORKSPACE / "build" / "python_reference"
os.environ["MPLCONFIGDIR"] = str(BUILD / "_environment" / "matplotlib")
sys.path.insert(0, str(PROJECT / "software" / "python"))
sys.path.insert(0, str(PROJECT / "verification" / "python"))
import numpy as np
import python_speech_features as psf
from mfcc_reference import load_profile, compute_mfcc, coefficient_tables
from checks import (synthetic_inputs, compare_stages, package_reference,
                    package_native_output, analytic_checks, input_contract_checks)
from plots import plot_development

KST = timezone(timedelta(hours=9))
PROFILE_NAMES = ("comparison_raw13", "github_static13")
UNITS = {
    "input_float": "PCM amplitude / profile.pcm_divisor",
    "preemphasis": "input amplitude", "frames": "input amplitude",
    "windowed": "input amplitude", "fft": "unscaled complex DFT of windowed amplitude",
    "power": "squared input amplitude / FFT length (no doubling of one-sided bins)",
    "mel_energies": "sum of power times unit-height Mel weights",
    "log_mel": "natural log of profile-conditioned Mel energy",
    "dct": "orthonormal DCT-II C0..C12 before lifter/energy replacement",
    "frame_energy": "sum of one-sided power bins",
    "mfcc": "profile output C0..C12 (github coefficient 0 is log frame energy)",
    "frame_starts": "zero-based input sample index (whole clip, no crop)",
}


def now():
    return datetime.now(KST).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def git(*args):
    p = subprocess.run(["git", "-C", str(PROJECT), *args], capture_output=True, text=True)
    return {"exit_code": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


def code_snapshot():
    files = [Path(__file__), PROJECT / "scripts/setup_python_reference.ps1",
             PROJECT / "docs/MFCC_SPEC.md"]
    for folder in ("software/python", "verification/python"):
        files += [p for p in (PROJECT / folder).rglob("*")
                  if p.is_file() and p.suffix in (".py", ".json", ".txt")]
    return {p.relative_to(PROJECT).as_posix(): sha(p) for p in sorted(set(files))}


def environment():
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("The pinned runtime uses Python 3.11")
    packages = {}
    for line in (PROJECT / "software/python/requirements.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, wanted = line.split("==")
        installed = metadata.version(name)
        if installed != wanted:
            raise RuntimeError(f"Package version mismatch: {name} {installed} != {wanted}")
        packages[name] = installed
    pkg_sources = {Path(p).name: sha(p) for p in
                   (psf.__file__, psf.base.__file__, psf.sigproc.__file__)}
    return {"python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "packages": packages,
            "package_source_hashes": pkg_sources,
            "threads": {n: os.environ[n] for n in
                        ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}}


def record_provenance(out):
    records = {}
    for name in ("python_speech_features", "numpy", "scipy", "matplotlib"):
        dist = metadata.distribution(name)
        meta = dist.metadata
        # Preserve full installed metadata/license notices outside the code repository.
        text_path = out / "provenance" / f"{name}_METADATA.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(str(meta), encoding="utf-8")
        notices = []
        for f in dist.files or []:
            if any(token in f.name.upper() for token in ("LICENSE", "LICENCE", "COPYING")):
                source = Path(dist.locate_file(f))
                if source.is_file():
                    notices.append({"installed_path": str(source), "sha256": sha(source)})
        records[name] = {"version": dist.version, "homepage": meta.get("Home-page"),
                         "project_urls": meta.get_all("Project-URL"),
                         "license_expression": meta.get("License-Expression"),
                         "license_metadata_file": str(text_path.relative_to(out)),
                         "installed_notices": notices}
    dump(out / "provenance/packages.json", records)
    notebook = WORKSPACE / "reference_code/github_mfcc/Python source/DNN modeling.ipynb"
    nb = read_json(notebook)
    dump(out / "provenance/github_notebook_settings.json", {
        "source": str(notebook), "sha256": sha(notebook),
        "snapshot_commit": "27aa09974049d11f49383c8e18f3ca9e34d08ed9",
        "cell_indices_zero_based": {str(i): "".join(nb["cells"][i]["source"])
                                    for i in (3, 10, 13)},
        "scope": "Static 13-dimensional settings check only. Original library/resampy versions unknown; no RTL, DNN or 39D VAD validation."})


def save_arrays(folder, arrays, profile, *, coefficient=False):
    folder.mkdir(parents=True, exist_ok=True)
    schema = {"storage": "headerless little-endian, C row-major",
              "profile_id": profile["profile_id"], "arrays": {}}
    for name, value in arrays.items():
        a = np.asarray(value)
        if np.iscomplexobj(a):
            dtype = np.dtype("<c16")
            axes = ["frame", "frequency_bin"]
        elif np.issubdtype(a.dtype, np.integer):
            dtype = np.dtype("<i8")
            axes = ["frame"] if name == "frame_starts" else ["index"]
        else:
            dtype = np.dtype("<f8")
            axes = (["sample"] if a.ndim == 1 else ["frame", "element"])
        if not coefficient and a.ndim == 2:
            axes = ["frame", "coefficient" if name in ("dct", "mfcc") else
                    "mel_filter" if name in ("mel_energies", "log_mel") else
                    "frequency_bin" if name in ("fft", "power") else "frame_sample"]
        if not coefficient and name == "frame_energy":
            axes = ["frame"]
        if coefficient:
            axes = {"window": ["frame_sample"], "mel_edges": ["mel_edge"],
                    "mel_filters": ["mel_filter", "frequency_bin"],
                    "dct_cosine": ["coefficient", "mel_filter"],
                    "dct_matrix": ["coefficient", "mel_filter"],
                    "dct_scale": ["coefficient"], "lifter": ["coefficient"]}[name]
        data = np.ascontiguousarray(a, dtype=dtype)
        target = folder / f"{name}.bin"
        data.tofile(target)
        schema["arrays"][name] = {"file": target.name, "shape": list(data.shape),
            "dtype": dtype.str, "axes": axes, "unit": UNITS.get(name, "coefficient table"),
            "sha256": sha(target), "bytes": target.stat().st_size,
            "finite": bool(np.isfinite(data).all()),
            "complex_layout": "interleaved float64 real,imag" if dtype.kind == "c" else None}
    dump(folder / "arrays.json", schema)
    if "mfcc" in arrays:
        np.savetxt(folder / "mfcc.csv", arrays["mfcc"], delimiter=",", fmt="%.17g",
                   header=",".join(profile["output_order"]), comments="")
    return schema


def expected_frames(t, profile):
    length, hop = profile["frame_length"], profile["frame_step"]
    if profile["frame_policy"] == "full_frames_only":
        return max(0, 1 + (t - length) // hop)
    return 1 if t <= length else 1 + (t - length + hop - 1) // hop


def load_clip(entry, dataset_dir):
    path = dataset_dir / entry["wav"]
    pcm_path = dataset_dir / entry["pcm"]
    if sha(path) != entry["wav_sha256"] or sha(pcm_path) != entry["pcm_sha256"]:
        raise ValueError(f"Input hash mismatch: {entry['utterance_id']}")
    with wave.open(str(path), "rb") as f:
        fmt = (f.getnchannels(), f.getframerate(), f.getsampwidth(), f.getcomptype())
        if fmt != (1, 16000, 2, "NONE"):
            raise ValueError(f"Unsupported WAV format: {fmt}")
        raw = f.readframes(f.getnframes())
    if hashlib.sha256(raw).hexdigest() != entry["pcm_sha256"] or raw != pcm_path.read_bytes():
        raise ValueError("WAV samples and canonical raw PCM differ")
    pcm = np.frombuffer(raw, dtype="<i2").copy()
    if (len(pcm) != entry["sample_count"] or entry["start_sample"] != 0 or
            entry["end_sample_exclusive"] != len(pcm)):
        raise ValueError("Input manifest count/region mismatch")
    for key, value in (("sample_rate_hz", 16000), ("channels", 1)):
        if entry[key] != value:
            raise ValueError(f"Manifest {key} mismatch")
    flac = dataset_dir / entry["source_flac"]
    if sha(flac) != entry["source_flac_sha256"]:
        raise ValueError("Original FLAC hash mismatch")
    return pcm


def all_pass(records):
    return all(v["passed"] for v in records.values())


def check_shapes(stages, pcm, profile):
    f = expected_frames(len(pcm), profile)
    shapes = {"input_float": (len(pcm),), "preemphasis": (len(pcm),),
        "frame_starts": (f,), "frames": (f, 512), "windowed": (f, 512),
        "fft": (f, 257), "power": (f, 257), "mel_energies": (f, 26),
        "log_mel": (f, 26), "dct": (f, 13), "frame_energy": (f,), "mfcc": (f, 13)}
    correct = all(np.asarray(stages[k]).shape == s for k, s in shapes.items())
    ordered = np.array_equal(stages["frame_starts"], np.arange(f, dtype=np.int64)*160)
    return {"passed": bool(correct and ordered), "expected_shapes": shapes,
            "frame_starts_exact": bool(ordered), "frames": f}


def process_input(pcm, label, role, out, profiles, tolerance, *, entry=None, figures=False):
    dest = out / role / label
    dest.mkdir(parents=True, exist_ok=False)
    pcm.astype("<i2").tofile(dest / "input_s16le.pcm")
    item = {"id": label, "role": role, "sample_count": len(pcm),
            "pcm_sha256": sha(dest / "input_s16le.pcm"), "input_manifest_entry": entry,
            "profiles": {}, "passed": True}
    for name, profile in profiles.items():
        atol, rtol = (tolerance["profiles"][name][k] for k in ("atol", "rtol"))
        if name == "github_static13" and len(pcm) == 0:
            item["profiles"][name] = {"status": "unsupported_empty_input_by_package",
                                      "passed": None}
            continue
        actual = compute_mfcc(pcm, profile)
        reference = package_reference(pcm, profile)
        checks = compare_stages(actual, reference, atol, rtol)
        shape_check = check_shapes(actual, pcm, profile)
        # Apply the same scalar supplements to development and evaluation.
        analytic = analytic_checks(pcm, profile, actual, coefficient_tables(profile), atol, rtol)
        schema = save_arrays(dest / name, actual, profile)
        comparison = dest / name / "package_comparison"
        comparison.mkdir()
        np.save(comparison / "adapted_mfcc.npy", reference["mfcc"], allow_pickle=False)
        native = package_native_output(pcm, profile) if len(pcm) else None
        native_check = None
        policy = None
        if native is not None:
            np.save(comparison / "native_mfcc.npy", native, allow_pickle=False)
            if name == "github_static13":
                native_check = {"mfcc": compare_stages(actual, {**reference, "mfcc": native}, atol, rtol)["mfcc"]}
            else:
                count = actual["mfcc"].shape[0]
                delta = native[:count] - actual["mfcc"]
                policy = {"status": "diagnostic_policy_difference_not_acceptance_test",
                    "native_frames": int(native.shape[0]), "comparison_frames": count,
                    "extra_padded_frames": int(native.shape[0] - count),
                    "main_floor_active_values": int(np.count_nonzero(actual["mel_energies"] < 1e-12)),
                    "shared_frames_max_abs": float(np.max(np.abs(delta))) if delta.size else 0.0,
                    "shared_frames_rmse": float(np.sqrt(np.mean(delta**2))) if delta.size else 0.0,
                    "explanation": "Native call: normalized samples, ceplifter=0, appendEnergy=False; package zero-only epsilon and ceil tail padding retained. Adapter: keep complete frames and apply max(E,1e-12) before DCT. No installed package edits."}
        passed = (all_pass(checks) and shape_check["passed"] and
                  all(r["passed"] for r in analytic) and
                  all(d["finite"] for d in schema["arrays"].values()) and
                  (native_check is None or all_pass(native_check)))
        item["profiles"][name] = {"status": "passed" if passed else "failed", "passed": bool(passed),
            "shape_check": shape_check, "stages": checks, "analytic_checks": analytic,
            "native_package_comparison": native_check, "native_package_policy": policy,
            "c_handoff_schema": str((dest / name / "arrays.json").relative_to(out))}
        if figures and name == "comparison_raw13":
            item["figures"] = [str(p.relative_to(out)) for p in plot_development(
                actual["mfcc"], reference["mfcc"], out / "figures", label)]
        item["passed"] = bool(item["passed"] and passed)
    dump(dest / "validation.json", item)
    print(f"{role}: {label}: {'PASS' if item['passed'] else 'FAIL'}", flush=True)
    return item


def summary(items):
    stages = {}
    for item in items:
        for profile, result in item["profiles"].items():
            for stage, stat in result.get("stages", {}).items():
                key = f"{profile}/{stage}"
                if key not in stages:
                    stages[key] = {"worst_max_abs": 0.0, "worst_clip_rmse": 0.0,
                                   "worst_clip": None, "passed": True}
                s = stages[key]
                if stat.get("max_abs") is None or stat.get("rmse") is None:
                    s["passed"] = False
                    continue
                if stat["max_abs"] >= s["worst_max_abs"]:
                    s["worst_max_abs"], s["worst_clip"] = stat["max_abs"], item["id"]
                s["worst_clip_rmse"] = max(s["worst_clip_rmse"], stat["rmse"])
                s["passed"] = bool(s["passed"] and stat["passed"])
    return {"input_count": len(items), "passed": all(i["passed"] for i in items),
            "passed_count": sum(i["passed"] for i in items), "stages": stages,
            "comparison_frame_count": sum(i["profiles"]["comparison_raw13"]["shape_check"]["frames"] for i in items)}


def export_metrics(out, items):
    import csv
    with (out / "stage_errors.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["role", "clip", "profile", "stage", "max_abs", "rmse", "passed"])
        writer.writeheader()
        for item in items:
            for profile, result in item["profiles"].items():
                for stage, stat in result.get("stages", {}).items():
                    writer.writerow({"role": item["role"], "clip": item["id"], "profile": profile,
                                     "stage": stage, **{k: stat.get(k) for k in ("max_abs", "rmse", "passed")}})


def run(args, out):
    env = environment()
    source = code_snapshot()
    ds = read_json(args.dataset_manifest)
    inputs = ds["clips"]
    dev = [e for e in inputs if e["role"] == "development"]
    evaluation = [e for e in inputs if e["role"] == "evaluation"]
    if len(dev) != 1 or len(evaluation) != 20 or len(inputs) != 21:
        raise ValueError("Expected exactly one development and twenty evaluation clips")
    if len({e["utterance_id"] for e in inputs}) != 21:
        raise ValueError("Duplicate clip IDs")
    if {e["speaker_id"] for e in dev} & {e["speaker_id"] for e in evaluation}:
        raise ValueError("Development/evaluation speaker overlap")
    profiles = {n: load_profile(PROJECT / "software/python/profiles" / (n + ".json")) for n in PROFILE_NAMES}
    tolerance = read_json(PROJECT / "verification/python/tolerances.json")
    manifest = {"schema_version": 1, "started_at_kst": now(), "status": "running",
        "phase": args.phase, "command_argv": [sys.executable, *sys.argv],
        "command_powershell": "& " + " ".join("'"+s.replace("'", "''")+"'" for s in [sys.executable, *sys.argv]),
        "working_directory": os.getcwd(), "output": str(out), "environment": env,
        "git_head": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain=v1"),
        "source_hashes": source, "spec_sha256": sha(PROJECT / "docs/MFCC_SPEC.md"),
        "dataset_manifest": str(args.dataset_manifest.resolve()), "dataset_manifest_sha256": sha(args.dataset_manifest),
        "dataset_spec_snapshot_sha256_unchanged": ds["mfcc_spec_snapshot_sha256"],
        "data_source": {k: ds[k] for k in ("dataset", "original_partition", "source_url", "license", "license_url", "selection_method")},
        "limitations": ["Model and package share numerical libraries; scalar formulas supplement comparison.",
                        "No ARM, RTL, FPGA, timing, power or recognition-accuracy measurement."]}
    dump(out / "run_manifest.json", manifest)
    for relative in source:
        snapshot = out / "provenance" / "source_snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / relative, snapshot)
    record_provenance(out)
    shutil.copyfile(args.dataset_manifest, out / "dataset_manifest_snapshot.json")
    shutil.copyfile(PROJECT / "docs/MFCC_SPEC.md", out / "MFCC_SPEC_snapshot.md")
    coefficients = {}
    for n, p in profiles.items():
        dump(out / "profiles" / (n + ".json"), p)
        coefficients[n] = save_arrays(out / "coefficients" / n, coefficient_tables(p), p, coefficient=True)
    dump(out / "tolerances.json", tolerance)
    coefficients_hash = {n: {k: a["sha256"] for k, a in s["arrays"].items()} for n, s in coefficients.items()}
    binding = {"source_hashes": source, "environment": env,
               "dataset_manifest_sha256": sha(args.dataset_manifest),
               "coefficient_hashes": coefficients_hash}
    items = []
    try:
        if args.phase in ("develop", "all"):
            contracts = {n: input_contract_checks(compute_mfcc, p) for n, p in profiles.items()}
            dump(out / "input_contract_checks.json", contracts)
            if not all(r["passed"] for group in contracts.values() for r in group):
                raise RuntimeError("Input contract validation failed")
            synthetic = []
            for name, pcm in synthetic_inputs().items():
                synthetic.append(process_input(pcm, name, "synthetic", out, profiles, tolerance))
            pcm = load_clip(dev[0], args.dataset_manifest.parent)
            development = process_input(pcm, dev[0]["utterance_id"], "development", out, profiles,
                                        tolerance, entry=dev[0], figures=True)
            if development["profiles"]["comparison_raw13"]["shape_check"]["frames"] != 534:
                development["passed"] = False
                development["expected_development_frames_failure"] = True
            items = synthetic + [development]
            manifest["development_summary"] = summary([development])
            manifest["synthetic_summary"] = summary(synthetic)
            export_metrics(out, items)
            dump(out / "development_gate.json", {"passed": all(i["passed"] for i in items),
                 "completed_at_kst": now(), "synthetic": manifest["synthetic_summary"],
                 "development": manifest["development_summary"]})
            if not all(i["passed"] for i in items):
                raise RuntimeError("Development/synthetic gate failed; evaluation not opened")
            if code_snapshot() != source or sha(args.dataset_manifest) != binding["dataset_manifest_sha256"]:
                raise RuntimeError("Source/spec/input changed before freeze")
            freeze = {**binding, "frozen_at_kst": now(), "development_run": str(out),
                      "development_gate_sha256": sha(out / "development_gate.json"),
                      "tolerance": tolerance, "passed": True}
            dump(out / "freeze.json", freeze)
        else:
            if args.development_run is None:
                raise ValueError("--phase evaluate requires --development-run")
            gate = read_json(args.development_run / "development_gate.json")
            freeze = read_json(args.development_run / "freeze.json")
            development_manifest = read_json(args.development_run / "run_manifest.json")
            if development_manifest["status"] != "passed":
                raise ValueError("Development run did not complete successfully")
            if not gate["passed"] or not freeze["passed"]:
                raise ValueError("Development gate did not pass")
            if sha(args.development_run / "development_gate.json") != freeze["development_gate_sha256"]:
                raise ValueError("Development gate changed after freeze")
            if any(freeze[k] != v for k, v in binding.items()):
                raise ValueError("Configuration/source/environment changed after development freeze; run development again")
            shutil.copyfile(args.development_run / "freeze.json", out / "freeze.json")
        manifest["freeze_sha256"] = sha(out / "freeze.json")
        if args.phase in ("all", "evaluate"):
            manifest["evaluation_started_at_kst"] = now()
            evaluations = []
            for entry in evaluation:
                if code_snapshot() != source:
                    raise RuntimeError("Code/spec changed during run")
                pcm = load_clip(entry, args.dataset_manifest.parent)
                expected = expected_frames(len(pcm), profiles["comparison_raw13"])
                if expected != entry["frame_count_if_L512_H160_no_padding"]:
                    raise ValueError("Dataset frame count differs from frozen profile")
                evaluations.append(process_input(pcm, entry["utterance_id"], "evaluation", out,
                                                  profiles, tolerance, entry=entry))
            items.extend(evaluations)
            manifest["evaluation_summary"] = summary(evaluations)
        export_metrics(out, items)
        if code_snapshot() != source or sha(args.dataset_manifest) != manifest["dataset_manifest_sha256"]:
            raise RuntimeError("Source/spec/input manifest mutated during run")
        if not all(i["passed"] for i in items):
            raise RuntimeError("Numerical validation failed (see per-clip validation.json)")
        manifest["status"] = "passed"
    except Exception:
        manifest["status"] = "failed"
        manifest["error"] = traceback.format_exc()
        export_metrics(out, items)
        raise
    finally:
        manifest["completed_at_kst"] = now()
        dump(out / "run_manifest.json", manifest)
        # Artifact hashes include actual stages, figures and validation, not this self-referential index.
        dump(out / "artifact_manifest.json", {p.relative_to(out).as_posix(): sha(p)
             for p in sorted(out.rglob("*")) if p.is_file() and p.name not in ("artifact_manifest.json", "console.log")})
    print(json.dumps({"status": manifest["status"], "output": str(out),
                     "development": manifest.get("development_summary"),
                     "evaluation": manifest.get("evaluation_summary")}, ensure_ascii=False), flush=True)


class Tee:
    def __init__(self, first, second):
        self.first, self.second = first, second
    def write(self, value):
        self.first.write(value)
        self.second.write(value)
    def flush(self):
        self.first.flush()
        self.second.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, default=WORKSPACE / "data/librispeech/DATASET_MANIFEST.json")
    parser.add_argument("--phase", choices=("develop", "evaluate", "all"), default="all")
    parser.add_argument("--development-run", type=Path)
    parser.add_argument("--run-id", default=datetime.now(KST).strftime("%Y%m%dT%H%M%S%f"))
    args = parser.parse_args()
    if not args.run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.run_id):
        parser.error("run-id must contain only letters, digits, underscores or hyphens")
    out = BUILD / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    os.environ["MPLCONFIGDIR"] = str(BUILD / "_environment" / "matplotlib")
    stdout, stderr = sys.stdout, sys.stderr
    with (out / "console.log").open("w", encoding="utf-8") as log:
        sys.stdout, sys.stderr = Tee(stdout, log), Tee(stderr, log)
        try:
            run(args, out)
        except Exception:
            traceback.print_exc()
            return 1
        finally:
            sys.stdout, sys.stderr = stdout, stderr
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
