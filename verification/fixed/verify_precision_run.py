#!/usr/bin/env python3
"""Read-only independent audit of a completed, full schema-2 precision study.

No study/model helpers are imported.  Integer Power/Mel and saved error
statistics are reconstructed from dumps, frozen references and equations.
The completed run is never modified; --report must name a new outside file.

Usage: python verification/fixed/verify_precision_run.py RUN_DIRECTORY
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("fft", "power", "mel_energies", "log_mel", "mfcc")
CONFIGS = {"d16", "d20_out16_in16", "d18_out18_in16", "d20_out20_in16",
           "d18_out18_in18", "d20_out20_in20"}
COUNTS = {"development": 1, "synthetic": 17, "extra_synthetic": 6}
BASELINE_HASH = "87bb1ea7d956ba8ea91b3e635d77c53739b387a313522809a38621cd25a0438a"


class Audit:
    def __init__(self, run):
        self.run = run
        self.checks = 0
        self.integer_dumps = 0
        self.integer_values = 0
        self.max_accumulator = 0
        self.ablation_paths = 0
        self.summaries = {}

    def require(self, condition, label):
        self.checks += 1
        if not condition:
            raise ValueError(label)

    @staticmethod
    def digest(path):
        h = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    def hashed(self, path, digest):
        self.require(path.is_file() and self.digest(path) == digest, f"SHA mismatch: {path}")

    def local(self, relative):
        path = (self.run / relative).resolve()
        self.require(path.is_relative_to(self.run), f"artifact path escapes run: {relative}")
        return path

    def integers(self, meta, shape):
        path = self.local(Path("arrays") / meta["file"])
        self.hashed(path, meta["sha256"])
        self.require(meta["shape"] == list(shape), f"integer shape: {path}")
        if meta["format"] == "<i8":
            self.require(path.stat().st_size == math.prod(shape) * 8, f"integer bytes: {path}")
            values = np.fromfile(path, dtype="<i8").tolist()
        elif meta["format"] == "decimal text":
            values = [int(word) for word in path.read_text(encoding="utf-8").split()]
            self.require(any(v < -(1 << 63) or v >= (1 << 63) for v in values),
                         f"text fallback should reflect int64 overflow: {path}")
        else:
            raise ValueError(f"unsupported integer format: {meta['format']}")
        self.require(len(values) == math.prod(shape), f"integer count: {path}")
        self.require((min(values, default=0), max(values, default=0)) == (meta["min"], meta["max"]),
                     f"integer min/max: {path}")
        self.integer_dumps += 1
        self.integer_values += len(values)
        return [values[i * shape[1]:(i + 1) * shape[1]] for i in range(shape[0])]

    def npz(self, meta):
        path = self.local(Path("arrays") / meta["file"])
        self.hashed(path, meta["sha256"])
        with np.load(path, allow_pickle=False) as saved:
            self.require(set(saved.files) == set(meta["arrays"]), f"NPZ array names: {path}")
            result = {name: saved[name].copy() for name in saved.files}
        for name, value in result.items():
            info = meta["arrays"][name]
            self.require(list(value.shape) == info["shape"] and value.dtype.str == info["dtype"],
                         f"NPZ shape/dtype: {path}/{name}")
            self.require(np.all(np.isfinite(value)), f"nonfinite NPZ: {path}/{name}")
        return result

    def arrays_equal(self, actual, expected, label, exact=True):
        self.require(np.shape(actual) == np.shape(expected), f"shape: {label}")
        matches = np.array_equal(actual, expected) if exact else np.allclose(
            actual, expected, atol=2e-12, rtol=1e-12)
        self.require(matches, f"array mismatch: {label}")

    def metric(self, actual, reference, recorded, label):
        error = np.abs(actual - reference)
        expected = {"max_abs": float(error.max()) if error.size else 0.0,
                    "rmse": math.sqrt(float(np.mean(error ** 2))) if error.size else 0.0}
        for name, value in expected.items():
            self.require(math.isclose(recorded[name], value, abs_tol=1e-13, rel_tol=1e-12),
                         f"metric mismatch: {label}/{name}")

    def metrics(self, stages, ref, entry, ablation=False):
        for name in STAGES:
            key = "mel" if ablation and name == "mel_energies" else name
            recorded = entry[key] if ablation else entry["stages"][key]
            self.metric(stages[name], ref[name], recorded, name)
        self.require(len(entry["per_coefficient"]) == 13, "coefficient metric count")
        for c, metric in enumerate(entry["per_coefficient"]):
            self.require(metric["coefficient"] == f"C{c}", "coefficient order")
            self.metric(stages["mfcc"][:, c], ref["mfcc"][:, c], metric, f"C{c}")
        self.require(entry["mel_cells_at_floor"] == int(np.sum(stages["mel_energies"] < 1e-12)),
                     "floor count")
        self.require(entry["floor_regressions"] == int(np.sum(
            (ref["mel_energies"] > 1e-12) & (stages["mel_energies"] < 1e-12))), "floor regression")

    def summarize(self, case, target, key, entry):
        summary = self.summaries.setdefault(f"{target}/{key}", {})
        group = "development" if case["group"] == "development" else "synthetic_all"
        for group_name in (group, "all_inputs"):
            acc = summary.setdefault(group_name, {
                "inputs": 0, "frames": 0, "mfcc_max_abs": 0.0,
                "worst_input_by_max_abs": None, "floor_regressions": 0,
                "clipped_frame_samples": 0, "clip_events_total": 0,
                "fft_overflow_frames": 0, "shift_diff_vs_input16_frames": 0,
                "_squared_error_sum": 0.0})
            metric = entry["stages"]["mfcc"]
            acc["inputs"] += 1
            acc["frames"] += case["frames"]
            acc["_squared_error_sum"] += metric["rmse"] ** 2 * case["frames"] * 13
            if acc["worst_input_by_max_abs"] is None or metric["max_abs"] > acc["mfcc_max_abs"]:
                acc["mfcc_max_abs"] = metric["max_abs"]
                acc["worst_input_by_max_abs"] = case["id"]
            for name in ("floor_regressions", "fft_overflow_frames", "shift_diff_vs_input16_frames"):
                acc[name] += entry[name]
            for name in ("clipped_frame_samples", "clip_events_total"):
                acc[name] += entry["clipping"][name]

    def verify(self):
        manifest = json.loads(self.local("run_manifest.json").read_text(encoding="utf-8"))
        self.require(manifest["schema_version"] == 2 and manifest["status"] == "completed", "completed schema2")
        hashes = json.loads(self.local("artifact_hashes.json").read_text(encoding="utf-8"))
        actual_files = {p.relative_to(self.run).as_posix() for p in self.run.rglob("*") if p.is_file()}
        self.require(actual_files == set(hashes) | {"artifact_hashes.json"}, "artifact hash inventory")
        for name, digest in hashes.items():
            self.hashed(self.local(name), digest)
        for name, digest in manifest["source_snapshot_sha256"].items():
            self.hashed(self.local(Path("source_snapshot") / name), digest)
        self.require(manifest["preserved_fft_sha256"] == BASELINE_HASH, "preserved model identity")
        self.hashed(self.local("source_snapshot/software/fixed_model/fft_bitmodel.py"), BASELINE_HASH)
        self.require(manifest["input_counts"] == COUNTS, "input counts 1/17/6")
        self.require(len(manifest["inputs"]) == len(manifest["part4_width_results"]) == 24, "24 inputs/results")
        self.require(dict(Counter(i["group"] for i in manifest["inputs"])) == COUNTS, "actual input groups")
        self.require(set(manifest["targets"]) == {"t0p5", "t0p975"}, "two targets")
        coefficients = self.npz(manifest["frozen_coefficient_dump"])
        coeff_root = (Path(manifest["reference_run"]) / "coefficients" / "comparison_raw13").resolve()
        for name, meta in manifest["reference_coefficients"]["arrays"].items():
            path = Path(meta["file"]).resolve()
            self.require(path.is_relative_to(coeff_root), f"coefficient reference path: {path}")
            self.hashed(path, meta["actual_sha256"])
            self.require(meta["actual_sha256"] == meta["recorded_sha256"], "coefficient recorded hash")
            original = np.fromfile(path, dtype=meta["dtype"]).reshape(meta["shape"])
            self.arrays_equal(coefficients[name], original, f"frozen coefficient {name}")
        self.hashed(Path(manifest["profile"]["file"]), manifest["profile"]["sha256"])
        dct = coefficients["dct_matrix"]
        weights = self.integers(manifest["mel_integer_dump"], (26, 257))
        fw = manifest["mel_fw"]
        self.require(all(0 <= v <= (1 << fw) for row in weights for v in row), "Mel weight bounds")
        self.arrays_equal(np.asarray(weights, dtype=object),
                          np.rint(coefficients["mel_filters"] * (1 << fw)).astype(object), "Mel coefficient quantization")
        sparse = [[(k, v) for k, v in enumerate(row) if v] for row in weights]
        sums = [sum(row) for row in weights]
        self.require(sums == manifest["mel_table"]["per_band_weight_sum_int"], "Mel weight sums")
        inputs = {entry["id"]: entry for entry in manifest["inputs"]}
        self.require(len(inputs) == 24, "input ids unique")
        self.require({entry["id"] for entry in manifest["part4_width_results"]} == set(inputs),
                     "width results cover each input exactly once")
        references = {}
        frame_counts = {}
        for cid, info in inputs.items():
            pcm_path = self.local(Path("arrays") / info["pcm"]["file"])
            self.hashed(pcm_path, info["pcm"]["sha256"])
            self.require(pcm_path.stat().st_size == 2 * info["pcm"]["samples"], f"PCM bytes: {cid}")
            frame_counts[cid] = max(0, 1 + (info["pcm"]["samples"] - 512) // 160)
            if info["group"] == "extra_synthetic":
                references[cid] = self.npz(info["generated_reference"])
            else:
                self.require(info["group"] in ("development", "synthetic"), "evaluation input prohibited")
                allowed = (Path(manifest["reference_run"]) / info["group"] / cid / "comparison_raw13").resolve()
                references[cid] = {}
                for name, meta in info["reference_hashes"]["arrays"].items():
                    path = Path(meta["file"]).resolve()
                    self.require(path.is_relative_to(allowed), f"unexpected frozen reference path: {path}")
                    self.hashed(path, meta["actual_sha256"])
                    self.require(meta["actual_sha256"] == meta["recorded_sha256"], "frozen recorded hash")
                    self.require(path.stat().st_size == math.prod(meta["shape"]) * np.dtype(meta["dtype"]).itemsize,
                                 f"frozen reference bytes: {path}")
                    references[cid][name] = np.fromfile(path, dtype=meta["dtype"]).reshape(meta["shape"])
            self.require(references[cid]["mfcc"].shape == (frame_counts[cid], 13), f"full frame count: {cid}")
        total_frames = sum(frame_counts.values())
        self.require(total_frames == manifest["total_frames_per_candidate"], "total frames")
        self.require(frame_counts["8463-294828-0037"] == 534, "all development frames")
        self.require(frame_counts["boundary_0"] == frame_counts["boundary_511"] == 0, "zero-frame cases retained")
        combination_count = 0
        shift_differences = Counter()
        for case in manifest["part4_width_results"]:
            cid, nf = case["id"], case["frames"]
            self.require(case["group"] == inputs[cid]["group"], "result input group")
            self.require(nf == frame_counts[cid], f"result frame count: {cid}")
            self.require(set(case["targets"]) == {"t0p5", "t0p975"}, "case targets")
            ref = references[cid]
            for target, configs in case["targets"].items():
                self.require(set(configs) == CONFIGS, "six width configs")
                base_shifts = None
                for key in ["d16", *sorted(CONFIGS - {"d16"})]:
                    entry = configs[key]
                    cfg = entry["config"]
                    widths = entry["widths"]
                    raw = {name: self.integers(meta, (nf, {"input_q": 512, "fft_re": 257, "fft_im": 257,
                                                         "psum": 257, "mel": 26, "shift": 1}[name]))
                           for name, meta in entry["integer_dumps"].items()}
                    self.require(set(raw) == {"input_q", "fft_re", "fft_im", "psum", "mel", "shift"}, "integer dump set")
                    if base_shifts is None:
                        base_shifts = raw["shift"]
                    differing = sum(a != b for a, b in zip(raw["shift"], base_shifts))
                    self.require(differing == entry["shift_diff_vs_input16_frames"], "BFP shift differences")
                    shift_differences[f"{target}/{key}"] += differing
                    histogram = dict(Counter(str(row[0]) for row in raw["shift"]))
                    self.require(histogram == entry["shift_histogram"], "BFP histogram")
                    s_fft = 4 * cfg["group_shift"] + cfg["trailing_shift"]
                    out_f, out_w = cfg["output_frac"], cfg["output_width"]
                    self.require(entry["physical_fft_shift_S"] == s_fft == 9, "physical FFT scale")
                    self.require(entry["output_requant_shift_bits"] == cfg["data_frac"] - out_f, "output F conversion")
                    self.require(entry["integer_code_shift_bits"] == s_fft + cfg["data_frac"] - out_f, "integer code shift")
                    psum_bound = 1 << (2 * out_w - 1)
                    acc_bound = psum_bound * max(sums)
                    self.require(widths["psum_width_unsigned"] == psum_bound.bit_length(), "Power unsigned width")
                    self.require(widths["mel_accumulator_width_unsigned"] == acc_bound.bit_length(), "Mel unsigned width")
                    self.require(widths["psum_max_theoretical"] == psum_bound, "Power theoretical bound")
                    self.require(widths["mel_weight_width_unsigned"] == fw + 1, "Mel weight width")
                    self.require(widths["mel_product_full_width_unsigned"] == psum_bound.bit_length() + fw + 1,
                                 "Mel full product width")
                    self.require(widths["mel_product_tight_width_unsigned"] == (psum_bound * (1 << fw)).bit_length(),
                                 "Mel tight product width")
                    stages = self.npz(entry["stage_dump"])
                    power = np.zeros((nf, 257))
                    mel = np.zeros((nf, 26))
                    fft = np.zeros((nf, 257), dtype=np.complex128)
                    for frame in range(nf):
                        re, im, s = raw["fft_re"][frame], raw["fft_im"][frame], raw["shift"][frame][0]
                        self.require(-2 <= s <= 24, "BFP shift bounds")
                        self.require(all(-(1 << (out_w - 1)) <= x < (1 << (out_w - 1)) for x in re + im), "FFT signed bounds")
                        self.require(all(abs(x) <= (1 << (entry["input_width"] - 1)) - 1 for x in raw["input_q"][frame]), "input symmetric bounds")
                        psum = [r * r + j * j for r, j in zip(re, im)]
                        self.require(psum == raw["psum"][frame] and max(psum) <= psum_bound, "exact integer Power")
                        accum = [sum(psum[k] * weight for k, weight in band) for band in sparse]
                        self.require(accum == raw["mel"][frame] and max(accum) <= acc_bound, "exact integer Mel")
                        self.max_accumulator = max(self.max_accumulator, max(accum))
                        p_exp = 2 * s_fft + 2 - 2 * s - 2 * out_f - 9
                        power[frame] = [float(v) * math.ldexp(1.0, p_exp) for v in psum]
                        mel[frame] = [float(v) * math.ldexp(1.0, p_exp - fw) for v in accum]
                        fft[frame] = (np.asarray(re, dtype=float) + 1j * np.asarray(im, dtype=float)) * math.ldexp(1.0, s_fft - out_f - s + 1)
                    for name, value in (("fft", fft), ("power", power), ("mel_energies", mel)):
                        self.arrays_equal(stages[name], value, f"{cid}/{target}/{key}/{name}")
                    log = np.log(np.maximum(mel, 1e-12))
                    self.arrays_equal(stages["log_mel"], log, "float64 log")
                    self.arrays_equal(stages["mfcc"], log @ dct.T, "float64 DCT", exact=False)
                    self.metrics(stages, ref, entry)
                    self.require(entry["fft_overflow_frames"] == len(set(entry["fft_overflow_frame_indices"])), "overflow frame metadata")
                    clipping = entry["clipping"]
                    self.require(clipping["clip_events_total"] == sum(clipping["clip_events_by_stage"].values()), "clipping event sum")
                    self.require(0 <= clipping["clipped_frame_samples"] <= min(nf * 512, clipping["clip_events_total"]), "clipping sample/event separation")
                    observed_psum = max((v for row in raw["psum"] for v in row), default=0)
                    observed_mel = max((v for row in raw["mel"] for v in row), default=0)
                    self.require(observed_psum == widths["psum_max_observed"], "observed Power max")
                    self.require(observed_mel == widths["mel_accumulator_max_observed"], "observed Mel max")
                    self.require(widths["mel_accumulator_fits_int64"] == (observed_mel < (1 << 63)), "observed int64 fit")
                    self.summarize(case, target, key, entry)
                    combination_count += 1
        ablations = manifest["part3_ablation_results"]
        self.require(len(ablations) == 48, "all cases/two targets ablation")
        self.require({(a["id"], a["target"]) for a in ablations} ==
                     {(cid, target) for cid in inputs for target in ("t0p5", "t0p975")},
                     "ablation cases/targets have no duplicates or omissions")
        for entry in ablations:
            ref = references[entry["id"]]
            for name, result in entry.items():
                if not isinstance(result, dict) or "stage_dump" not in result:
                    continue
                stages = self.npz(result["stage_dump"])
                self.metrics(stages, ref, result, ablation=True)
                if name == "A_float64_reference":
                    for stage in STAGES:
                        self.arrays_equal(stages[stage], ref[stage], f"ablation A/{stage}")
                self.ablation_paths += 1
        self.require(self.ablation_paths == 240, "five ablation paths per case/target")
        for summary in self.summaries.values():
            for group in summary.values():
                sse = group.pop("_squared_error_sum")
                group["mfcc_frame_weighted_rmse"] = math.sqrt(sse / (13 * group["frames"])) if group["frames"] else 0.0
        return {"status": "PASS", "run_id": manifest["run_id"], "run_directory": str(self.run),
                "audit_script": str(Path(__file__).resolve()), "audit_script_sha256": self.digest(Path(__file__)),
                "checks": self.checks, "hashed_artifacts": len(hashes),
                "source_snapshot_files": len(manifest["source_snapshot_sha256"]),
                "input_counts": COUNTS, "total_frames_per_candidate": total_frames,
                "zero_frame_inputs": [cid for cid, frames in frame_counts.items() if not frames],
                "width_target_combinations_per_input": 12, "case_combinations_checked": combination_count,
                "integer_dumps_checked": self.integer_dumps, "integer_values_checked": self.integer_values,
                "largest_mel_integer": self.max_accumulator,
                "ablation_paths_checked": self.ablation_paths,
                "bfp_shift_differences_vs_input16": dict(shift_differences),
                "candidate_summary": self.summaries,
                "scope": "Independent artifact/hash, integer Power/Mel, scale and metric audit; not an independent FFT algorithm or RTL validation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--report", type=Path, help="New JSON file outside the immutable run; otherwise stdout")
    args = parser.parse_args()
    run = args.run_directory.resolve()
    if args.report and (args.report.resolve().is_relative_to(run) or args.report.exists()):
        parser.error("report must be a new file outside the completed run")
    audit = Audit(run)
    try:
        result = audit.verify()
    except Exception as exc:
        result = {"status": "FAIL", "run_directory": str(run), "checks_before_failure": audit.checks,
                  "error": f"{type(exc).__name__}: {exc}"}
    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.report:
        with args.report.open("x", encoding="utf-8") as report:
            report.write(rendered + "\n")
    print(rendered)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
