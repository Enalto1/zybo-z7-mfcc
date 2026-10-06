"""Validate raw vendor runs and stitch frame-owned, overlapping replay segments.

No DSP calculation is used to replace the DUT. Frame zero of noninitial segments
is checked for protocol/finite output, then excluded from numerical comparison.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np

STAGES = {0: "input_float", 10: "preemphasis", 11: "frames", 12: "windowed",
          1: "fft", 2: "power", 3: "mel_energies", 4: "log_mel", 5: "dct", 6: "mfcc"}
ORDER = [0, 10, 11, 12, 1, 2, 3, 4, 5, 6]
WIDTH = {11: 512, 12: 512, 1: 257, 2: 257, 3: 26, 4: 26, 5: 13, 6: 13}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def plan_segments(samples: int, workers: int) -> list[dict]:
    frames = max(0, 1 + (samples - 512) // 160)
    if not 1 <= workers <= frames:
        raise ValueError("Worker count must be between 1 and the full-frame count")
    result = []
    for number in range(workers):
        first, stop = number * frames // workers, (number + 1) * frames // workers
        origin_frame = max(0, first - 1)
        origin = origin_frame * 160
        end = samples if stop == frames else stop * 160 + 512
        count = end - origin
        local_frames = 1 + (count - 512) // 160
        result.append({"number": number, "owned_frame_first": first, "owned_frame_stop": stop,
            "origin_sample": origin, "end_sample": end, "samples": count,
            "origin_frame": origin_frame, "frames": local_frames,
            "warmup_frames": int(first > 0), "extra_boundary_frames": int(stop < frames),
            "owned_sample_first": first * 160, "owned_sample_stop": samples if stop == frames else stop * 160})
    if sum(s["owned_frame_stop"] - s["owned_frame_first"] for s in result) != frames:
        raise AssertionError("Frame ownership is incomplete")
    if sum(s["owned_sample_stop"] - s["owned_sample_first"] for s in result) != samples:
        raise AssertionError("Sample ownership is incomplete")
    return result


def validate_worker(directory: Path, segment: dict) -> tuple[dict, dict]:
    """Require every raw local transaction, including warmup and audit frames."""
    arrays, counts = {}, {}
    for stage in ORDER:
        shape = (segment["frames"], WIDTH[stage]) if stage in WIDTH else (segment["samples"],)
        if stage == 1:
            shape += (2,)
        arrays[stage] = np.empty(shape, dtype="<u4")
        counts[stage] = 0
    raw = (directory / "traces.txt").read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Completed worker trace has a trailing incomplete record")
    last_cycle = -1
    for line_number, line in enumerate(raw.splitlines(), 1):
        words = line.split()
        if len(words) != 7:
            raise ValueError(f"Malformed worker trace line {line_number}")
        number, stage, frame, index = map(int, words[:4])
        if number != segment["number"] or stage not in arrays:
            raise ValueError(f"Unexpected local case/stage at line {line_number}")
        count = counts[stage]
        width = WIDTH.get(stage, segment["samples"])
        expected = (count // width, count % width) if stage in WIDTH else (0, count)
        limit = segment["frames"] * width if stage in WIDTH else segment["samples"]
        if (frame, index) != expected or count >= limit:
            raise ValueError(f"Local order/count/duplicate violation at line {line_number}")
        values = [int(word, 16) for word in words[4:6]]
        if any(word < 0 or word > 0xffffffff or (word & 0x7f800000) == 0x7f800000 for word in values):
            raise ValueError(f"Local nonfinite/invalid word at line {line_number}, including warmup")
        if stage == 1:
            arrays[stage].reshape(-1, 2)[count] = values
        else:
            if values[1] != 0:
                raise ValueError("Unexpected nonzero auxiliary field")
            arrays[stage].flat[count] = values[0]
        cycle = int(words[6])
        if cycle < last_cycle:
            raise ValueError("Local cycle order moved backwards")
        last_cycle = cycle
        counts[stage] += 1
    for stage in ORDER:
        expected = segment["frames"] * WIDTH[stage] if stage in WIDTH else segment["samples"]
        if counts[stage] != expected:
            raise ValueError(f"Local stage {STAGES[stage]} count {counts[stage]}/{expected}")
    if not np.array_equal(arrays[5], arrays[6]):
        raise ValueError("Raw local DCT/MFCC bits differ")
    summary = (directory / "simulation_summary.txt").read_text()
    expected_prefix = (f"PASS {segment['number']} samples={segment['samples']} frames={segment['frames']} "
                       f"coeffs={segment['frames'] * 13} cycles=")
    lines = summary.splitlines()
    if len(lines) != 2 or not lines[0].startswith(expected_prefix) or lines[1] != "ALL_PROTOCOL_PASS cases=1":
        raise ValueError("Local simulation protocol summary missing or inconsistent")
    cycles = int(lines[0][len(expected_prefix):])
    return arrays, {"number": segment["number"], "protocol_passed": True,
        "all_local_frames_validated": segment["frames"], "raw_stage_counts": {STAGES[k]: v for k, v in counts.items()},
        "local_cycles": cycles, "trace_sha256": sha(directory / "traces.txt"),
        "summary_sha256": sha(directory / "simulation_summary.txt")}


def stitch(raw: list[dict], segments: list[dict], samples: int) -> tuple[dict, dict]:
    frames = max(0, 1 + (samples - 512) // 160)
    merged = {}
    for stage in ORDER:
        shape = (frames, WIDTH[stage]) if stage in WIDTH else (samples,)
        if stage == 1:
            shape += (2,)
        merged[stage] = np.empty(shape, dtype="<u4")
    frame_coverage = np.zeros(frames, dtype=np.uint8)
    sample_coverage = np.zeros(samples, dtype=np.uint8)
    sample_bits, sample_seen = {}, {}
    for stage in (0, 10):
        sample_bits[stage] = np.empty(samples, dtype="<u4")
        sample_seen[stage] = np.zeros(samples, dtype=bool)
    frame_seen = np.zeros(frames, dtype=bool)
    frame_bits = {stage: np.empty_like(merged[stage]) for stage in WIDTH}
    boundary_checks = 0
    overlap_sample_checks = {0: 0, 10: 0}
    for values, segment in zip(raw, segments, strict=True):
        a, b, origin = segment["owned_frame_first"], segment["owned_frame_stop"], segment["origin_sample"]
        local_a, local_b = a - segment["origin_frame"], b - segment["origin_frame"]
        frame_coverage[a:b] += 1
        for stage in WIDTH:
            merged[stage][a:b] = values[stage][local_a:local_b]
        sa, sb = segment["owned_sample_first"], segment["owned_sample_stop"]
        sample_coverage[sa:sb] += 1
        for stage in (0, 10):
            merged[stage][sa:sb] = values[stage][sa-origin:sb-origin]
            # Only the first preemphasis sample of a noninitial segment has
            # zero predecessor. Input conversion has no such exception.
            omit = int(stage == 10 and origin > 0)
            indices = np.arange(origin + omit, segment["end_sample"])
            candidate = values[stage][omit:]
            overlap = sample_seen[stage][indices]
            if not np.array_equal(sample_bits[stage][indices[overlap]], candidate[overlap]):
                raise ValueError(f"Overlapping {STAGES[stage]} bits differ")
            overlap_sample_checks[stage] += int(overlap.sum())
            sample_bits[stage][indices] = candidate
            sample_seen[stage][indices] = True
        for local_frame in range(segment["warmup_frames"], segment["frames"]):
            global_frame = segment["origin_frame"] + local_frame
            if not 0 <= global_frame < frames:
                raise ValueError("Local retained/audit frame maps outside original clip")
            if frame_seen[global_frame]:
                for stage in WIDTH:
                    if not np.array_equal(frame_bits[stage][global_frame], values[stage][local_frame]):
                        raise ValueError(f"Boundary frame {global_frame} {STAGES[stage]} bits differ")
                boundary_checks += 1
            for stage in WIDTH:
                frame_bits[stage][global_frame] = values[stage][local_frame]
            frame_seen[global_frame] = True
    if not np.all(frame_coverage == 1) or not np.all(sample_coverage == 1) or not np.all(frame_seen):
        raise ValueError("Global sample/frame ownership has a hole or duplicate")
    if boundary_checks != len(segments) - 1:
        raise ValueError("Missing cross-segment frame boundary checks")
    return merged, {"unique_owned_frames": frames, "unique_owned_samples": samples,
        "frame_ownership_min": int(frame_coverage.min()), "frame_ownership_max": int(frame_coverage.max()),
        "sample_ownership_min": int(sample_coverage.min()), "sample_ownership_max": int(sample_coverage.max()),
        "boundary_frames_bit_equal": boundary_checks,
        "boundary_frame_stages": [STAGES[k] for k in WIDTH],
        "overlap_sample_elements_bit_equal": {STAGES[k]: v for k, v in overlap_sample_checks.items()},
        "warmup_frames_excluded_from_numerical_comparison": sum(s["warmup_frames"] for s in segments)}


def compare_segmented(out: Path) -> dict:
    out = Path(out)
    freeze = json.loads((out / "freeze.json").read_text())
    spec = importlib.util.spec_from_file_location("segmented_frozen_compare", out / "source/verification/fp32/compare.py")
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    raw, local_records = [], []
    for segment in freeze["segments"]:
        arrays, record = validate_worker(out / "workers" / f"segment_{segment['number']:02d}", segment)
        raw.append(arrays)
        local_records.append(record)
    case = freeze["case"]
    merged, mapping = stitch(raw, freeze["segments"], case["samples"])
    mapping["all_warmup_frames_protocol_and_finite_validated"] = True
    tolerances = json.loads((out / "tolerances.json").read_text())["stages"]
    record = {"id": case["id"], "group": "development", "samples": case["samples"], "frames": case["frames"],
              "protocol_passed": True, "protocol_scope": "independent overlapping segments",
              "first_failing_stage": None, "stages": {}, "known_pc_c_failure": False}
    arraydir = out / "arrays" / case["id"]
    arraydir.mkdir(parents=True, exist_ok=False)
    index = {"storage": "headerless little-endian C row-major", "arrays": {}}
    refs, actuals, pcs = {}, {}, {}
    def verified_bytes(kind, path):
        path = Path(path)
        root = Path(freeze["python_root"] if kind == "python" else freeze["c_root"])
        key = kind + "/" + path.relative_to(root).as_posix()
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != freeze["reference_hashes"].get(key):
            raise ValueError(f"Frozen original reference changed: {key}")
        return data
    pyindex = json.loads(verified_bytes("python", Path(case["python_reference"], "arrays.json")))["arrays"]
    for stage in ORDER:
        name = STAGES[stage]
        dtype = "<c8" if stage == 1 else "<f4"
        bits = merged[stage]
        hw_native = bits.view(dtype).reshape(bits.shape[:-1]) if stage == 1 else bits.view(dtype)
        hw = hw_native.astype(np.complex128 if stage == 1 else np.float64)
        entry = pyindex[name]
        py = np.frombuffer(verified_bytes("python", Path(case["python_reference"]) / entry["file"]),
                           dtype=entry["dtype"]).reshape(entry["shape"])
        pc = np.frombuffer(verified_bytes("c", Path(case["c_reference"]) / (name + ".bin")),
                           dtype=dtype).reshape(hw.shape).astype(hw.dtype)
        if py.shape != hw.shape or not np.isfinite(py).all() or not np.isfinite(pc).all():
            raise ValueError("Original reference shape/finite mismatch")
        measurement = compare.metric(hw, py, tolerances[name])
        measurement["versus_pc_c"] = compare.metric(hw, pc, tolerances[name])
        bit_dtype = "<u8" if stage == 1 else "<u4"
        measurement["hw_vs_pc_bit_equal_elements"] = int(np.count_nonzero(hw_native.view(bit_dtype) == pc.astype(dtype).view(bit_dtype)))
        record["stages"][name] = measurement
        if not measurement["passed"] and record["first_failing_stage"] is None:
            record["first_failing_stage"] = name
        destination = arraydir / (name + ".bin")
        hw_native.tofile(destination)
        index["arrays"][name] = {"file": destination.name, "dtype": dtype, "shape": list(hw.shape),
                                "sha256": sha(destination), "finite": True}
        refs[stage], actuals[stage], pcs[stage] = py, hw, pc
    record["passed"] = record["first_failing_stage"] is None
    record["simulated_completion_interval_cycles"] = {"count": 0, "min": None, "median": None, "max": None,
        "note": "Independent segment clocks cannot establish single-clip throughput; raw local cycles remain in worker records."}
    record["coefficient_errors"] = compare.figures(out, case["id"] + "_segmented_replay", actuals[6], refs[6], pcs[6])
    result = {"schema_version": 1, "segmented_replay": True, "protocol_passed": True,
        "protocol_scope": "all raw local segment transactions including warmup/audit frames",
        "single_clip_protocol_passed": None, "single_clip_throughput_measured": False,
        "numeric_acceptance_passed": record["passed"], "evaluation_audio_read": False,
        "physical_board_accessed": False, "total_frames": case["frames"], "passed_cases": int(record["passed"]),
        "failed_cases": [] if record["passed"] else [case["id"]], "cases": [record], "mapping": mapping,
        "workers": local_records}
    dump(arraydir / "arrays.json", index)
    dump(arraydir / "validation.json", record)
    dump(out / "comparison.json", result)
    return result


def self_test() -> dict:
    """Exercise mapping without any DSP arithmetic or reference/audio access."""
    segments = plan_segments(85920, 8)
    assert sum(s["frames"] for s in segments) == 548
    assert sum(s["samples"] for s in segments) == 90624
    source = {}
    for stage in ORDER:
        shape = (534, WIDTH[stage]) if stage in WIDTH else (85920,)
        if stage == 1:
            shape += (2,)
        source[stage] = np.arange(np.prod(shape), dtype="<u4").reshape(shape)
    local = []
    for segment in segments:
        current = {}
        for stage in ORDER:
            if stage in WIDTH:
                start = segment["origin_frame"]
                current[stage] = source[stage][start:start+segment["frames"]].copy()
                if segment["warmup_frames"]:
                    current[stage][0] ^= np.uint32(0x10000000)
            else:
                current[stage] = source[stage][segment["origin_sample"]:segment["end_sample"]].copy()
                if stage == 10 and segment["warmup_frames"]:
                    current[stage][0] ^= np.uint32(0x10000000)
        local.append(current)
    merged, mapping = stitch(local, segments, 85920)
    assert all(np.array_equal(merged[stage], source[stage]) for stage in ORDER)
    local[1][6][1, 0] ^= np.uint32(1)
    rejected = False
    try:
        stitch(local, segments, 85920)
    except ValueError:
        rejected = True
    assert rejected
    return {"passed": True, "owned_frames": 534, "local_frames": 548, "local_samples": 90624,
            "boundary_corruption_rejected": rejected, "mapping": mapping}
