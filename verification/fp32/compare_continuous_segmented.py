"""Read-only provenance/bit comparison of the 534-frame continuous and segmented runs.

Only the new --output directory is written. The frozen segment parser is loaded
by its pinned hash, and only its read-only validate_worker/stitch functions run.
Neither the DSP nor the numerical acceptance thresholds are recomputed here.
Exit 0: identical stage bits; 2: differing bits; 1: invalid provenance/contract.
Use --self-test for in-memory rejection checks, or --validate-segmented alone
with --segmented-run for a read-only audit before a continuous run exists.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys

import numpy as np


STAGES = ("input_float", "preemphasis", "frames", "windowed", "fft", "power",
          "mel_energies", "log_mel", "dct", "mfcc")
STAGE_IDS = (0, 10, 11, 12, 1, 2, 3, 4, 5, 6)
CASE_ID, SAMPLES, FRAMES, PROFILE = "8463-294828-0037", 85920, 534, "comparison_raw13"
PCM_SHA = "026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32"
BASELINE_INDEX = {
    "python": "dbd2678abf8273642396edfa153eb642636e26bcdc69f9db40f06839deddb243",
    "c": "e4abda3effe428e36f055965179d33e75e86c3c751753625952c63f6b3c41d85",
}
SEGMENT_INDEX = "8af76152e3b00efd204be083d9c6bc59287253ffaae1fb46b970949b63985b7c"
PARSER_SHA = "5d9ee65173eda43d01fe565bdbe0b32e65632e8f76c617e996edb0ed77b8091f"
TOLERANCE_SHA = "64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6"
RTL = {f"hardware/fp32/rtl/{name}.sv" for name in (
    "fp32_alu", "fp32_backend", "fp32_frame_buffer", "fp32_mfcc", "fp32_preemphasis", "fp32_window")}
GENERATOR = "verification/fp32/generate_coefficients.py"
TB = "verification/fp32/tb_fp32_mfcc.sv"
RUNTIME_PREFIX = "project/fp32_mfcc.sim/sim_1/behav/xsim/"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"Duplicate JSON key {key}: {path}")
            value[key] = item
        return value
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def contained(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    require(path != root and path.is_relative_to(root), f"Path escapes root: {relative}")
    return path


class Bundle:
    def __init__(self, root, expected_index=None):
        self.root = Path(root).resolve()
        self.index_path = self.root / "artifact_manifest.json"
        self.index_sha = sha(self.index_path)
        if expected_index is not None:
            require(self.index_sha == expected_index, f"Artifact index identity changed: {self.root}")
        self.index = read_json(self.index_path)
        require(isinstance(self.index, dict) and self.index, f"Empty artifact index: {self.root}")
        self.checked = {}

    def file(self, relative, expected=None):
        path = contained(self.root, relative)
        digest = sha(path)
        require(self.index.get(relative) == digest, f"Missing/changed artifact binding: {path}")
        if expected is not None:
            require(digest == expected, f"Frozen identity changed: {path}")
        self.checked[relative] = digest
        return path

    def json(self, relative, expected=None):
        return read_json(self.file(relative, expected))

    def summary(self):
        return {"root": str(self.root), "artifact_manifest_sha256": self.index_sha,
                "verified_artifact_hashes": dict(sorted(self.checked.items()))}


def metadata(bundle, segmented):
    run, freeze = bundle.json("run_manifest.json"), bundle.json("freeze.json")
    states = ({"completed_segmented_passed", "completed_segmented_numerical_failures"}
              if segmented else {"passed", "completed_with_numerical_failures"})
    require(run.get("status") in states, f"Run not completed in expected format: {bundle.root}")
    require(freeze.get("status") == "frozen_before_execution", "Missing pre-execution freeze")
    require(bool(freeze.get("segmented_replay", False)) == segmented, "Wrong run format")
    for key, value in freeze.items():
        if key != "status":
            require(run.get(key) == value, f"Run/freeze mismatch: {key}")
    require(run.get("protocol_passed") is True, "Completed run lacks protocol acceptance")
    require(run.get("evaluation_audio_read") is False and run.get("physical_board_accessed") is False,
            "Expected simulation of development audio only")
    case_list = [freeze["case"]] if segmented else freeze["cases"]
    require(len(case_list) == 1, "Exactly one development clip is required")
    case = case_list[0]
    for key, expected in (("id", CASE_ID), ("samples", SAMPLES), ("frames", FRAMES), ("pcm_sha256", PCM_SHA)):
        require(case.get(key) == expected, f"Wrong canonical case {key}")
    if not segmented:
        require(case.get("number") == 0 and case.get("group") == "development", "Wrong continuous case identity")
        require(run.get("vivado_exit_code") == 0 and freeze.get("mode") in ("sim", "all"), "Vendor simulation did not complete")
        require(freeze.get("reset_stress") is False, "This comparison expects one uninterrupted clip job")
        detail = bundle.file("protocol_detail.txt")
        require(detail.stat().st_size > 0, "Empty continuous protocol detail")
    return run, freeze, case


def sources(bundle, hashes, prefix):
    require(isinstance(hashes, dict) and hashes, "Missing frozen source identities")
    for relative, digest in hashes.items():
        bundle.file(prefix + relative, digest)
    actual_rtl = {key for key in hashes if key.startswith("hardware/fp32/rtl/")}
    require(actual_rtl == RTL, "Unexpected RTL source set")
    require(GENERATOR in hashes and TB in hashes, "Missing generator/testbench source")
    return {key: hashes[key] for key in sorted(RTL | {GENERATOR})}


def shapes():
    return {"input_float": [SAMPLES], "preemphasis": [SAMPLES],
            "frames": [FRAMES, 512], "windowed": [FRAMES, 512],
            "fft": [FRAMES, 257], "power": [FRAMES, 257],
            "mel_energies": [FRAMES, 26], "log_mel": [FRAMES, 26],
            "dct": [FRAMES, 13], "mfcc": [FRAMES, 13]}


def check_array_contract(stage, entry, byte_count, reference=False):
    dtype = ("<c16" if stage == "fft" else "<f8") if reference else ("<c8" if stage == "fft" else "<f4")
    require(entry.get("file") == stage + ".bin" and entry.get("dtype") == dtype
            and entry.get("shape") == shapes()[stage], f"Wrong schema: {stage}")
    expected_bytes = math.prod(shapes()[stage]) * np.dtype(dtype).itemsize
    require(byte_count == expected_bytes, f"Wrong exact byte count/trailing bytes: {stage}")
    require(entry.get("finite") is True, f"Stage not recorded finite: {stage}")
    if "bytes" in entry:
        require(entry["bytes"] == expected_bytes, f"Wrong declared bytes: {stage}")
    return dtype


def references(freeze, case):
    bundles = {kind: Bundle(freeze[kind + "_root"], digest) for kind, digest in BASELINE_INDEX.items()}
    checked = {}
    def frozen(kind, path):
        root = bundles[kind].root
        path = Path(path).resolve()
        require(path.is_relative_to(root), "Reference path escapes frozen root")
        relative = path.relative_to(root).as_posix()
        digest = freeze["reference_hashes"].get(kind + "/" + relative)
        require(isinstance(digest, str), f"Reference absent from freeze: {path}")
        bundles[kind].file(relative, digest)
        checked[kind + "/" + relative] = digest
        return path
    py = Path(case["python_reference"])
    pc = Path(case["c_reference"])
    schema = read_json(frozen("python", py / "arrays.json"))
    require(schema.get("profile_id") == PROFILE, "Wrong reference profile")
    pcm = frozen("python", py.parent / "input_s16le.pcm").read_bytes()
    require(len(pcm) == SAMPLES * 2 and hashlib.sha256(pcm).hexdigest() == PCM_SHA, "Canonical PCM bytes changed")
    for stage in STAGES:
        p = frozen("python", py / (stage + ".bin"))
        dtype = check_array_contract(stage, schema["arrays"][stage], p.stat().st_size, True)
        require(sha(p) == schema["arrays"][stage]["sha256"], f"Python schema hash mismatch: {stage}")
        require(np.isfinite(np.fromfile(p, dtype=dtype)).all(), f"Nonfinite Python reference: {stage}")
        p = frozen("c", pc / (stage + ".bin"))
        dtype = "<c8" if stage == "fft" else "<f4"
        require(p.stat().st_size == math.prod(shapes()[stage]) * np.dtype(dtype).itemsize, f"C shape/bytes mismatch: {stage}")
        require(np.isfinite(np.fromfile(p, dtype=dtype)).all(), f"Nonfinite C reference: {stage}")
    return {"profile_id": PROFILE, "reference_hashes": checked,
            "baseline_artifact_index_sha256": BASELINE_INDEX}, pcm


def coefficients(bundle, freeze):
    coeff = bundle.json("coefficients/coefficient_manifest.json")
    require(coeff.get("profile_id") == PROFILE and coeff.get("mel_nonzero_words") == 459,
            "Expected compact raw13 coefficients")
    require(set(coeff["tables"]) == {"window", "mel", "mel_compact", "mel_descriptor", "dct_cosine", "dct_scale"},
            "Wrong coefficient table set")
    cbase = Bundle(freeze["c_root"], BASELINE_INDEX["c"])
    for relative, digest in coeff["verified_source_artifacts"].items():
        if relative == "artifact_manifest.json":
            require(digest == cbase.index_sha, "Coefficient C baseline differs")
        else:
            cbase.file(relative, digest)
    for entry in coeff["tables"].values():
        p = bundle.file("coefficients/" + entry["file"], entry["sha256"])
        words = p.read_text(encoding="ascii").splitlines()
        require(len(words) == entry["physical_words"] and all(re.fullmatch(r"[0-9a-fA-F]{8}", w) for w in words),
                f"Invalid ROM content/length: {p}")
        cbase.file(entry["source_file"], entry["source_sha256"])
    require(coeff.get("mel_full_scatter_roundtrip_bit_mismatches") == 0
            and coeff.get("mel_ordered_mac_word_mismatches") == 0, "Compact coefficient audit failed")
    return coeff


def ip_identity(freeze):
    root = Path(freeze["ip_run"]).resolve()
    path = root / "ip_manifest.json"
    require(sha(path) == freeze["ip_manifest_sha256"], "Vendor IP manifest changed")
    manifest = read_json(path)
    require(manifest.get("target_part") == "xc7z020clg400-1" and manifest.get("aclken_enabled") is True,
            "Unexpected IP target/CE configuration")
    require(bool(manifest.get("output_hashes")), "Empty IP output manifest")
    for relative, digest in manifest["output_hashes"].items():
        require(sha(contained(root, relative)) == digest, f"Vendor IP output changed: {relative}")
    return {"manifest_sha256": sha(path), "root": str(root), "verified_output_files": len(manifest["output_hashes"])}


def saved_arrays(bundle):
    base = f"arrays/{CASE_ID}/"
    schema = bundle.json(base + "arrays.json")
    require(set(schema["arrays"]) == set(STAGES), "Wrong actual ten-stage set")
    files = {p.name for p in (bundle.root / base).glob("*.bin")}
    require(files == {s + ".bin" for s in STAGES}, "Extra/missing binary stage files")
    all_files = {p.relative_to(bundle.root).as_posix() for p in (bundle.root / "arrays").rglob("*.bin")}
    require(all_files == {base + s + ".bin" for s in STAGES}, "Unexpected additional case arrays")
    values, records = {}, {}
    for stage in STAGES:
        entry = schema["arrays"][stage]
        path = bundle.file(base + stage + ".bin", entry["sha256"])
        dtype = check_array_contract(stage, entry, path.stat().st_size)
        require(np.isfinite(np.fromfile(path, dtype=dtype)).all(), f"Nonfinite actual stage: {stage}")
        values[stage] = np.fromfile(path, dtype="<u4")
        records[stage] = {"file": base + stage + ".bin", "sha256": entry["sha256"],
                          "shape": shapes()[stage], "dtype": dtype, "bytes": path.stat().st_size,
                          "binary32_component_words": int(values[stage].size)}
    require(np.array_equal(values["dct"], values["mfcc"]), "Accepted MFCC differs from DCT bits")
    return values, records


def comparison_record(bundle, run, tolerances):
    comparison = bundle.json("comparison.json")
    require(comparison.get("protocol_passed") is True and comparison.get("total_frames") == FRAMES,
            "Comparison lacks complete protocol/frame record")
    require(len(comparison["cases"]) == 1, "Comparison case count differs")
    case = comparison["cases"][0]
    require(bundle.json(f"arrays/{CASE_ID}/validation.json") == case, "Validation/comparison record differs")
    require(case.get("id") == CASE_ID and case.get("samples") == SAMPLES and case.get("frames") == FRAMES,
            "Comparison case identity differs")
    require(set(case["stages"]) == set(STAGES), "Comparison stage set differs")
    for stage in STAGES:
        for record in (case["stages"][stage], case["stages"][stage]["versus_pc_c"]):
            require(record.get("elements") == math.prod(shapes()[stage]) and record.get("nonfinite") == 0,
                    f"Comparison count/finite record differs: {stage}")
            require(record.get("tolerance") == tolerances["stages"][stage], f"Comparison tolerance differs: {stage}")
    require(run.get("numeric_acceptance_passed") == comparison.get("numeric_acceptance_passed") == case.get("passed"),
            "Recorded numerical acceptance differs")
    return comparison


def input_mem(path, pcm):
    lines = path.read_text(encoding="ascii").splitlines()
    require(len(lines) * 2 == len(pcm) and all(re.fullmatch(r"[0-9a-fA-F]{4}", x) for x in lines), "Wrong PCM hex transport")
    actual = np.array([int(x, 16) for x in lines], dtype="<u2").tobytes()
    require(actual == pcm, "PCM transport bit mismatch")


def segment_plan():
    result = []
    for n in range(8):
        first, stop = n * FRAMES // 8, (n + 1) * FRAMES // 8
        origin_frame = max(0, first - 1)
        origin = origin_frame * 160
        end = SAMPLES if stop == FRAMES else stop * 160 + 512
        result.append({"number": n, "owned_frame_first": first, "owned_frame_stop": stop,
            "origin_frame": origin_frame, "origin_sample": origin, "end_sample": end,
            "samples": end - origin, "frames": 1 + (end - origin - 512) // 160,
            "warmup_frames": int(first > 0), "extra_boundary_frames": int(stop < FRAMES),
            "owned_sample_first": first * 160, "owned_sample_stop": SAMPLES if stop == FRAMES else stop * 160})
    return result


def check_segment_plan(freeze):
    require(freeze.get("workers") == 8 and len(freeze["segments"]) == 8, "Expected eight segments")
    for actual, expected in zip(freeze["segments"], segment_plan(), strict=True):
        require(all(type(actual.get(k)) is int and actual[k] == v for k, v in expected.items()), "Segment ownership/span changed")
    require(freeze.get("pcm_ownership_reconstruction_bit_mismatches") == 0, "Original PCM ownership reconstruction failed")


def protocol_detail(text, samples=SAMPLES, frames=FRAMES):
    """Check the new TB's lifecycle counters without combining worker clocks."""
    fields = {
        "PROTOCOL_DETAIL": "case samples frames coeffs pcm_accepts input_converted pre_accepts frame_words window_words tail_discarded final_frame final_start final_coeff",
        "LIFECYCLE": "case start_accepts eof_accepts done_accepts total_reset_assertions initial_reset_low_clocks resets_during_case done_hold_clocks post_done_idle_clocks",
        "COVERAGE": "case stimulus_gap_events input_gap_clocks input_stall_clocks pre_stall_clocks frame_stall_clocks window_stall_clocks output_stall_clocks output_last_stall_clocks forced_last_stall_events",
        "CYCLES": "case clip_start_accept last_pcm_accept eof_accept last_c12_accept first_done_valid done_accept protocol_elapsed post_idle_end",
    }
    lines = text.splitlines()
    require(text.endswith("\n") and len(lines) == 5 and lines[-1] == "ALL_PROTOCOL_DETAIL_PASS cases=1",
            "Incomplete/extra continuous protocol detail records")
    parsed = {}
    for line, (label, names) in zip(lines[:4], fields.items(), strict=True):
        words = line.split()
        require(words and words[0] == label, f"Unexpected protocol record order: {label}")
        record = {}
        for word in words[1:]:
            match = re.fullmatch(r"([a-z0-9_]+)=([0-9]+)", word)
            require(match is not None, f"Malformed protocol field: {word}")
            key, value = match.groups()
            require(key not in record, f"Duplicate protocol field: {key}")
            record[key] = int(value)
        require(set(record) == set(names.split()) and record["case"] == 0, f"Missing/unknown protocol keys: {label}")
        parsed[label] = record
    detail = parsed["PROTOCOL_DETAIL"]
    expected = {"case": 0, "samples": samples, "frames": frames, "coeffs": frames * 13,
        "pcm_accepts": samples, "input_converted": samples, "pre_accepts": samples,
        "frame_words": frames * 512, "window_words": frames * 512,
        "tail_discarded": samples - ((frames - 1) * 160 + 512),
        "final_frame": frames - 1, "final_start": (frames - 1) * 160, "final_coeff": 12}
    require(detail == expected, "Continuous accepted sample/frame/tail/final coefficient counts differ")
    life = parsed["LIFECYCLE"]
    for key, expected in (("start_accepts", 1), ("eof_accepts", 1), ("done_accepts", 1),
                          ("total_reset_assertions", 1), ("initial_reset_low_clocks", 16),
                          ("resets_during_case", 0), ("post_done_idle_clocks", 64)):
        require(life[key] == expected, f"Continuous lifecycle mismatch: {key}")
    require(life["done_hold_clocks"] >= 13, "Completion was not held for 13 clocks")
    coverage = parsed["COVERAGE"]
    require(coverage["forced_last_stall_events"] == frames, "C12 stall coverage is incomplete")
    for key in ("stimulus_gap_events", "input_gap_clocks", "input_stall_clocks",
                "output_stall_clocks", "output_last_stall_clocks"):
        require(coverage[key] > 0, f"Required input gap/backpressure coverage absent: {key}")
    require(coverage["output_stall_clocks"] >= coverage["output_last_stall_clocks"] >= frames,
            "Output stall subset counts differ")
    cycles = parsed["CYCLES"]
    require(0 < cycles["clip_start_accept"] < cycles["last_pcm_accept"] < cycles["eof_accept"],
            "Input/start/EOF cycle ordering differs")
    require(cycles["clip_start_accept"] < cycles["last_c12_accept"]
            and max(cycles["eof_accept"], cycles["last_c12_accept"]) < cycles["first_done_valid"],
            "Completion preceded EOF or the final accepted C12")
    require(cycles["done_accept"] - cycles["first_done_valid"] == life["done_hold_clocks"],
            "Completion hold cycles disagree with event timestamps")
    require(cycles["post_idle_end"] - cycles["done_accept"] == life["post_done_idle_clocks"],
            "Post-completion idle duration disagrees with timestamps")
    require(0 < cycles["protocol_elapsed"] < cycles["post_idle_end"], "Invalid protocol interval")
    return parsed


def load_parser(bundle, freeze):
    relative = "verification/fp32/segment_compare.py"
    require(freeze["source_hashes"].get(relative) == PARSER_SHA, "Unrecognized raw trace validator")
    path = bundle.file("source/" + relative, PARSER_SHA)
    spec = importlib.util.spec_from_file_location("verified_readonly_segment_parser", path)
    module = importlib.util.module_from_spec(spec)
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


def inspect_segmented(root):
    bundle = Bundle(root, SEGMENT_INDEX)
    run, freeze, case = metadata(bundle, True)
    check_segment_plan(freeze)
    for relative, digest in freeze["source_hashes"].items():
        bundle.file("source/" + relative, digest)
    design = sources(bundle, freeze["compiled_source_hashes"], "compiled_source/")
    refs, pcm = references(freeze, case)
    arrays, array_records = saved_arrays(bundle)
    tolerances = bundle.json("tolerances.json", TOLERANCE_SHA)
    require(freeze["tolerance_sha256"] == TOLERANCE_SHA, "Frozen tolerance identity differs")
    comparison = comparison_record(bundle, run, tolerances)
    snapshot = Bundle(freeze["snapshot_run"], freeze["snapshot_artifact_index_sha256"])
    snap_run = snapshot.json("run_manifest.json", freeze["snapshot_run_manifest_sha256"])
    snap_freeze = snapshot.json("freeze.json", freeze["snapshot_freeze_sha256"])
    require(snap_run.get("status") == "passed" and snap_freeze.get("status") == "frozen_before_execution", "Unverified compiled snapshot")
    require(snap_freeze["source_hashes"] == freeze["compiled_source_hashes"], "Compiled source linkage differs")
    sources(snapshot, snap_freeze["source_hashes"], "source/")
    coeff = coefficients(snapshot, snap_freeze)
    require(snapshot.checked["coefficients/coefficient_manifest.json"] == freeze["coefficient_manifest_sha256"], "Coefficient snapshot differs")
    ip = ip_identity(snap_freeze)
    require(ip["manifest_sha256"] == freeze["ip_manifest_sha256"], "Segmented IP identity differs")
    parser = load_parser(bundle, freeze)
    processes = run["processes"]
    require(sorted(p["segment"] for p in processes) == list(range(8)) and all(p["exit_code"] == 0 for p in processes), "Worker completion differs")
    raw, checks = [], []
    for segment in freeze["segments"]:
        prefix = f"workers/segment_{segment['number']:02d}/"
        directory = bundle.root / prefix
        for relative, digest in freeze["runtime_hashes"].items():
            snapshot.file(RUNTIME_PREFIX + relative, digest)
            bundle.file(prefix + relative, digest)
        for relative, digest in segment["prepared_input_hashes"].items():
            bundle.file(prefix + relative, digest)
        for entry in coeff["tables"].values():
            require(freeze["runtime_hashes"].get(entry["file"]) == entry["sha256"], "Worker ROM/manifest mismatch")
        sliced = pcm[2 * segment["origin_sample"]:2 * segment["end_sample"]]
        require((directory / "input_s16le.pcm").read_bytes() == sliced, "Worker PCM slice differs")
        require(hashlib.sha256(sliced).hexdigest() == segment["pcm_slice_sha256"], "Worker PCM slice identity differs")
        input_mem(directory / "input.mem", sliced)
        require(segment.get("hex_roundtrip_bit_mismatches") == 0
                and sha(directory / "input.mem") == segment["hex_sha256"], "Worker transport audit differs")
        bundle.file(prefix + "traces.txt")
        bundle.file(prefix + "simulation_summary.txt")
        values, record = parser.validate_worker(directory, segment)
        raw.append(values)
        checks.append(record)
    require(checks == comparison["workers"], "Revalidated raw workers differ from saved comparison")
    merged, mapping = parser.stitch(raw, freeze["segments"], SAMPLES)
    mapping["all_warmup_frames_protocol_and_finite_validated"] = True
    require(mapping == comparison["mapping"], "Revalidated ownership/boundary report differs")
    require(mapping["unique_owned_frames"] == FRAMES and mapping["unique_owned_samples"] == SAMPLES
            and mapping["boundary_frames_bit_equal"] == 7, "Incomplete mapped clip")
    for name, sid in zip(STAGES, STAGE_IDS, strict=True):
        require(np.array_equal(merged[sid].ravel(), arrays[name]), f"Saved segmented array differs from raw ownership mapping: {name}")
    return {"bundle": bundle, "freeze": freeze, "arrays": arrays, "array_records": array_records,
            "design": design, "coeff": coeff, "ip": ip, "references": refs, "parser": parser,
            "report": {"status": run["status"], "numeric_acceptance_passed": run["numeric_acceptance_passed"],
                "mapping": mapping, "raw_local_frames_revalidated": sum(s["frames"] for s in freeze["segments"]),
                "raw_local_samples_revalidated": sum(s["samples"] for s in freeze["segments"]),
                "snapshot_provenance": snapshot.summary(), "testbench_sha256": freeze["compiled_source_hashes"][TB]}}


def inspect_continuous(root, segmented, expected_index=None):
    bundle = Bundle(root, expected_index)
    run, freeze, case = metadata(bundle, False)
    design = sources(bundle, freeze["source_hashes"], "source/")
    require(design == segmented["design"], "RTL or coefficient generator differs")
    refs, pcm = references(freeze, case)
    require(refs == segmented["references"], "Original PCM/Python/C references differ")
    coeff = coefficients(bundle, freeze)
    require(coeff == segmented["coeff"], "Coefficient manifest/bits/constants differ")
    for entry in coeff["tables"].values():
        bundle.file(RUNTIME_PREFIX + entry["file"], entry["sha256"])
    ip = ip_identity(freeze)
    require(ip["manifest_sha256"] == segmented["ip"]["manifest_sha256"], "Vendor IP configuration/model identity differs")
    tolerances = bundle.json("tolerances.json", TOLERANCE_SHA)
    require(freeze["tolerance_sha256"] == TOLERANCE_SHA, "Continuous frozen tolerances differ")
    comparison_record(bundle, run, tolerances)
    path = bundle.file(f"inputs/{CASE_ID}.mem", case["hex_sha256"])
    input_mem(path, pcm)
    require(case.get("input_transport_bit_mismatches") == 0, "Continuous input transport not verified")
    arrays, array_records = saved_arrays(bundle)
    bundle.file("traces.txt")
    bundle.file("simulation_summary.txt")
    detail = protocol_detail(bundle.file("protocol_detail.txt").read_text(encoding="ascii"))
    raw, raw_record = segmented["parser"].validate_worker(bundle.root, {"number": 0, "samples": SAMPLES, "frames": FRAMES})
    require(raw_record["local_cycles"] == detail["CYCLES"]["protocol_elapsed"], "Summary/detail interval differs")
    # The last coefficient event is near the trace end; bound the extra read.
    with (bundle.root / "traces.txt").open("rb") as stream:
        stream.seek(max(0, stream.seek(0, 2) - 512 * 1024))
        tail = stream.read().splitlines()
    final_lines = [line.split() for line in tail if line.startswith(b"0 6 533 12 ")]
    require(len(final_lines) == 1 and int(final_lines[0][-1]) == detail["CYCLES"]["last_c12_accept"],
            "Final C12 timestamp differs from the accepted raw trace")
    for name, sid in zip(STAGES, STAGE_IDS, strict=True):
        require(np.array_equal(raw[sid].ravel(), arrays[name]), f"Continuous saved array differs from accepted raw trace: {name}")
    return {"bundle": bundle, "freeze": freeze, "arrays": arrays, "array_records": array_records,
            "design": design, "coeff": coeff, "ip": ip, "references": refs,
            "report": {"status": run["status"], "numeric_acceptance_passed": run["numeric_acceptance_passed"],
                "raw_frames_revalidated": FRAMES, "raw_samples_revalidated": SAMPLES,
                "raw_stage_counts": raw_record["raw_stage_counts"],
                "continuous_protocol_detail_passed": True, "protocol_detail": detail,
                "testbench_sha256": freeze["source_hashes"][TB]}}


def word_comparison(a, b):
    require(a.dtype == b.dtype == np.dtype("<u4") and a.shape == b.shape, "Binary32 word array contract differs")
    where = np.flatnonzero(a != b)
    first = int(where[0]) if where.size else None
    return {"binary32_component_words": int(a.size), "word_bit_mismatches": int(where.size),
            "first_mismatch_flat_component_index": first,
            "continuous_word_hex": f"{int(a[first]):08x}" if first is not None else None,
            "segmented_word_hex": f"{int(b[first]):08x}" if first is not None else None}


def self_test():
    rejected = []
    def rejects(label, operation):
        try:
            operation()
        except (ValueError, KeyError):
            rejected.append(label)
        else:
            raise AssertionError(f"Negative check accepted: {label}")
    good = {"file": "mfcc.bin", "dtype": "<f4", "shape": [FRAMES, 13], "finite": True}
    check_array_contract("mfcc", good, FRAMES * 13 * 4)
    for key, value in (("dtype", "<f8"), ("shape", [FRAMES - 1, 13]), ("file", "../mfcc.bin"), ("finite", False)):
        bad = dict(good, **{key: value})
        rejects("schema_" + key, lambda bad=bad: check_array_contract("mfcc", bad, FRAMES * 13 * 4))
    rejects("trailing_bytes", lambda: check_array_contract("mfcc", good, FRAMES * 13 * 4 + 1))
    plan = {"workers": 8, "segments": segment_plan(), "pcm_ownership_reconstruction_bit_mismatches": 0}
    check_segment_plan(plan)
    bad = copy.deepcopy(plan)
    bad["segments"][1]["owned_frame_first"] -= 1
    rejects("overlapping_ownership", lambda: check_segment_plan(bad))
    bad = copy.deepcopy(plan)
    bad["segments"][-1]["end_sample"] -= 128
    rejects("missing_tail_samples", lambda: check_segment_plan(bad))
    bad = copy.deepcopy(plan)
    bad["segments"][0]["extra_boundary_frames"] = 0
    rejects("missing_boundary_frame", lambda: check_segment_plan(bad))
    a = np.array([0, 0x3f800000], dtype="<u4")
    require(word_comparison(a, a.copy())["word_bit_mismatches"] == 0, "Equal bits rejected")
    b = a.copy()
    b[0] = 0x80000000
    require(word_comparison(a, b)["word_bit_mismatches"] == 1, "Signed zero mismatch hidden")
    rejects("word_shape", lambda: word_comparison(a, a[:1]))
    detail = (
        "PROTOCOL_DETAIL case=0 samples=85920 frames=534 coeffs=6942 pcm_accepts=85920 input_converted=85920 pre_accepts=85920 frame_words=273408 window_words=273408 tail_discarded=128 final_frame=533 final_start=85280 final_coeff=12\n"
        "LIFECYCLE case=0 start_accepts=1 eof_accepts=1 done_accepts=1 total_reset_assertions=1 initial_reset_low_clocks=16 resets_during_case=0 done_hold_clocks=13 post_done_idle_clocks=64\n"
        "COVERAGE case=0 stimulus_gap_events=1 input_gap_clocks=3 input_stall_clocks=3 pre_stall_clocks=0 frame_stall_clocks=0 window_stall_clocks=0 output_stall_clocks=1602 output_last_stall_clocks=1602 forced_last_stall_events=534\n"
        "CYCLES case=0 clip_start_accept=20 last_pcm_accept=100000 eof_accept=100001 last_c12_accept=99999 first_done_valid=100003 done_accept=100016 protocol_elapsed=99997 post_idle_end=100080\n"
        "ALL_PROTOCOL_DETAIL_PASS cases=1\n")
    protocol_detail(detail)
    # Both possible EOF/C12 orderings are allowed; done follows both.
    protocol_detail(detail.replace("last_c12_accept=99999", "last_c12_accept=100002"))
    for before, after in (("tail_discarded=128", "tail_discarded=0"), ("resets_during_case=0", "resets_during_case=1"),
                          ("eof_accepts=1", "eof_accepts=2"), ("input_stall_clocks=3", "input_stall_clocks=0"),
                          ("first_done_valid=100003", "first_done_valid=99998"),
                          ("post_idle_end=100080", "post_idle_end=100079"),
                          ("forced_last_stall_events=534", "forced_last_stall_events=533"),
                          ("ALL_PROTOCOL_DETAIL_PASS cases=1\n", ""),
                          ("samples=85920", "samples=85920 samples=85920")):
        rejects("protocol_" + before.split("=")[0], lambda before=before, after=after: protocol_detail(detail.replace(before, after)))
    return {"passed": True, "negative_checks_rejected": rejected,
            "signed_zero_bit_difference_detected": True, "audio_read": False, "run_files_written": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--continuous-run", type=Path)
    parser.add_argument("--segmented-run", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-continuous-index-sha256")
    parser.add_argument("--validate-segmented", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(not any((args.continuous_run, args.segmented_run, args.output, args.validate_segmented)), "Self-test takes no run arguments")
        print(json.dumps(self_test()))
        return 0
    require(args.segmented_run is not None, "--segmented-run is required")
    if args.validate_segmented:
        require(args.continuous_run is None and args.output is None, "Validation-only mode writes no output and takes no continuous run")
    else:
        require(args.continuous_run is not None and args.output is not None, "--continuous-run and --output are required")
        out = args.output.resolve()
        build = Path(__file__).resolve().parents[3] / "build/fp32_hw"
        roots = (args.continuous_run.resolve(), args.segmented_run.resolve())
        require(out.is_relative_to(build) and out != build and not out.exists()
                and not any(out.is_relative_to(r) or r.is_relative_to(out) for r in roots),
                "Use a separate new output directory below build/fp32_hw")
    segmented = inspect_segmented(args.segmented_run)
    if args.validate_segmented:
        print(json.dumps({"segmented_provenance_and_raw_mapping_passed": True,
                          "artifact_manifest_sha256": segmented["bundle"].index_sha,
                          "report": segmented["report"], "continuous_comparison_performed": False}))
        return 0
    continuous = inspect_continuous(args.continuous_run, segmented, args.expected_continuous_index_sha256)
    reports = {}
    for name in STAGES:
        report = word_comparison(continuous["arrays"][name], segmented["arrays"][name])
        report.update({"shape": shapes()[name], "dtype": continuous["array_records"][name]["dtype"],
                       "bytes": continuous["array_records"][name]["bytes"],
                       "continuous_sha256": continuous["array_records"][name]["sha256"],
                       "segmented_sha256": segmented["array_records"][name]["sha256"]})
        reports[name] = report
    identical = all(r["word_bit_mismatches"] == 0 for r in reports.values())
    result = {"schema_version": 1, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "comparison_provenance_passed": True, "all_stage_bits_identical": identical,
        "continuous_protocol_checks_passed": True, "segmented_mapping_checks_passed": True,
        "case_id": CASE_ID, "profile_id": PROFILE, "samples": SAMPLES, "frames": FRAMES,
        "pcm_sha256": PCM_SHA, "stage_count": len(STAGES), "stages": reports,
        "provenance": {kind: {**data["bundle"].summary(), **data["report"]}
                       for kind, data in (("continuous", continuous), ("segmented", segmented))},
        "identical_design_sources": continuous["design"], "reference_identity": continuous["references"],
        "ip_identity": continuous["ip"], "tolerance_sha256": TOLERANCE_SHA,
        "coefficient_manifest_sha256": segmented["freeze"]["coefficient_manifest_sha256"],
        "testbench_hashes_identical": continuous["report"]["testbench_sha256"] == segmented["report"]["testbench_sha256"],
        "scope": "Saved accepted stage bits for this development clip and checks of the continuous TB lifecycle counters. Each run's TB is independently bound. No cycle sums, throughput, board, power, accuracy, or whole-design equivalence claim.",
        "evaluation_audio_read": False, "physical_board_accessed": False,
        "tool_sha256": sha(__file__), "python_version": sys.version, "numpy_version": np.__version__}
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, out / "compare_continuous_segmented_used.py")
    (out / "equivalence.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out / "artifact_manifest.json").write_text(json.dumps({p.name: sha(p) for p in out.iterdir() if p.is_file()}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "comparison_provenance_passed": True,
                      "stage_count": len(reports), "all_stage_bits_identical": identical}))
    return 0 if identical else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({"comparison_provenance_passed": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
