"""Structural and numerical C binary32 versus pinned Python float64 checks."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


STAGE_ORDER = ("input_float", "preemphasis", "frame_starts", "frames", "windowed",
               "fft", "power", "mel_energies", "log_mel", "dct", "frame_energy", "mfcc")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_arrays(folder: Path, *, reference: bool = False) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Read descriptors; reject file-size, dtype and declared SHA mismatches."""
    folder = Path(folder).resolve()
    metadata_path = folder / ("arrays.json" if reference or not (folder / "host_manifest.json").exists() else "host_manifest.json")
    metadata = _read(metadata_path)
    reference_root, artifacts = None, None
    if reference:
        for parent in (folder, *folder.parents):
            if (parent / "artifact_manifest.json").is_file() and (parent / "run_manifest.json").is_file():
                reference_root = parent
                artifacts = _read(parent / "artifact_manifest.json")
                break
        if reference_root is None:
            raise ValueError("Reference arrays do not belong to a pinned run")
        relative = metadata_path.relative_to(reference_root).as_posix()
        if artifacts.get(relative) != _sha(metadata_path):
            raise ValueError("Reference array descriptor differs from pinned artifact index")
    if metadata.get("profile_id") != "comparison_raw13":
        raise ValueError("Expected comparison_raw13 output metadata")
    arrays = {}
    for name, descriptor in metadata["arrays"].items():
        path = (folder / descriptor["file"]).resolve()
        if not path.is_relative_to(folder):
            raise ValueError(f"Array path escapes case directory: {name}")
        dtype = np.dtype(descriptor["dtype"])
        expected_types = ({"<f8", "<c16", "<i8"} if reference else {"<f4", "<c8", "<u8", "<i8"})
        if descriptor["dtype"] not in expected_types:
            raise ValueError(f"Unexpected dtype {descriptor['dtype']} for {name}")
        shape = tuple(descriptor["shape"])
        if any(not isinstance(size, int) or size < 0 for size in shape):
            raise ValueError(f"Invalid shape: {name}")
        expected_bytes = math.prod(shape) * dtype.itemsize
        if descriptor["bytes"] != expected_bytes or path.stat().st_size != expected_bytes:
            raise ValueError(f"Byte count or element count mismatch: {name}")
        if descriptor.get("sha256") and _sha(path) != descriptor["sha256"]:
            raise ValueError(f"Array hash mismatch: {name}")
        if reference and artifacts.get(path.relative_to(reference_root).as_posix()) != _sha(path):
            raise ValueError(f"Reference array differs from pinned artifact index: {name}")
        arrays[name] = np.fromfile(path, dtype=dtype).reshape(shape)
    return arrays, metadata


def _metric(actual: np.ndarray, expected: np.ndarray, atol: float, rtol: float) -> dict[str, Any]:
    # Promote before subtraction. The complex error is |actual - expected|;
    # comparing |actual| alone would miss a conjugated FFT/sign error.
    actual = np.asarray(actual, dtype=np.complex128 if np.iscomplexobj(actual) else np.float64)
    expected = np.asarray(expected, dtype=np.complex128 if np.iscomplexobj(expected) else np.float64)
    if not (math.isfinite(atol) and math.isfinite(rtol) and atol >= 0 and rtol >= 0):
        raise ValueError("Tolerances must be finite and nonnegative")
    finite = bool(np.isfinite(actual).all() and np.isfinite(expected).all())
    result: dict[str, Any] = {"atol": atol, "rtol": rtol, "shape_ok": actual.shape == expected.shape,
        "finite": finite, "elements": int(expected.size), "max_abs": None, "rmse": None,
        "violations": None, "passed": False, "max_error_index": None}
    if not result["shape_ok"] or not finite:
        return result
    error = np.abs(actual - expected)
    failures = error > atol + rtol * np.abs(expected)
    result.update(max_abs=float(error.max()) if error.size else 0.0,
        rmse=float(np.sqrt(np.mean(error * error))) if error.size else 0.0,
        violations=int(failures.sum()), passed=not bool(failures.any()),
        max_error_index=list(np.unravel_index(int(np.argmax(error)), error.shape)) if error.size else None)
    nonzero = np.abs(expected) > 0.0
    result["max_relative_on_nonzero_reference"] = float(np.max(error[nonzero] / np.abs(expected[nonzero]))) if np.any(nonzero) else None
    result["nonzero_error_on_zero_reference"] = int(np.count_nonzero((~nonzero) & (error > 0.0)))
    if result["max_error_index"] is not None:
        result["max_error_index"] = [int(value) for value in result["max_error_index"]]
    return result


def _tolerance(tolerances: dict[str, Any], stage: str) -> tuple[float, float]:
    if stage == "frame_starts":
        return 0.0, 0.0
    selected = tolerances.get("stages", {}).get(stage, tolerances.get("default", tolerances))
    return float(selected["atol"]), float(selected["rtol"])


def compare_case(reference_dir: Path, c_dir: Path, tolerances: dict[str, Any]) -> dict[str, Any]:
    """Report structural failures separately from per-stage numerical errors."""
    report: dict[str, Any] = {"passed": False, "first_failed_stage": None,
        "reference_dir": str(Path(reference_dir).resolve()), "c_dir": str(Path(c_dir).resolve()),
        "structural": {}, "stages": {}, "per_coefficient_mfcc": []}
    try:
        reference, reference_meta = load_arrays(reference_dir, reference=True)
        actual, host = load_arrays(c_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        report["first_failed_stage"] = "structure"
        report["structural"] = {"passed": False, "error": str(error)}
        return report
    count = reference["mfcc"].shape[0]
    samples = reference["input_float"].size
    expected_count = 0 if samples < 512 else 1 + (samples - 512) // 160
    expected_shapes = {"input_float": (samples,), "preemphasis": (samples,),
        "frame_starts": (expected_count,), "frames": (expected_count, 512),
        "windowed": (expected_count, 512), "fft": (expected_count, 257),
        "power": (expected_count, 257), "mel_energies": (expected_count, 26),
        "log_mel": (expected_count, 26), "dct": (expected_count, 13),
        "frame_energy": (expected_count,), "mfcc": (expected_count, 13)}
    structural: dict[str, Any] = {
        "passed": True, "reference_frame_count": count, "expected_frame_count": expected_count,
        "sample_count": samples, "reference_count_correct": count == expected_count,
        "host_status": host.get("status"), "host_status_ok": host.get("status") == "passed",
        "host_frame_count": host.get("frame_count"), "host_sample_count": host.get("sample_count"),
        "host_count_ok": type(host.get("frame_count")) is int and host["frame_count"] == expected_count,
        "host_sample_count_ok": type(host.get("sample_count")) is int and host["sample_count"] == samples,
        "arrays": {},
    }
    structural["passed"] = all(structural[key] for key in
        ("reference_count_correct", "host_status_ok", "host_count_ok", "host_sample_count_ok"))
    for stage in STAGE_ORDER:
        if stage not in actual or stage not in reference:
            structural["arrays"][stage] = {"passed": False, "error": "required stage missing"}
            structural["passed"] = False
            continue
        a, ref = actual[stage], reference[stage]
        valid_dtype = (a.dtype.kind in "iu" and a.dtype.itemsize == 8 if stage == "frame_starts"
                       else a.dtype == np.dtype("<c8") if stage == "fft" else a.dtype == np.dtype("<f4"))
        good_shape = a.shape == ref.shape == expected_shapes[stage]
        finite = bool(np.isfinite(a).all() and np.isfinite(ref).all())
        record = {"passed": good_shape and valid_dtype and finite,
            "expected_shape": list(expected_shapes[stage]), "reference_shape": list(ref.shape),
            "actual_shape": list(a.shape), "shape_ok": good_shape, "dtype_ok": valid_dtype,
            "actual_dtype": a.dtype.str, "finite": finite}
        if stage == "frame_starts":
            record["order_exact"] = bool(np.array_equal(a, np.arange(expected_count, dtype=np.uint64) * 160)
                                          and np.array_equal(a, ref))
            record["passed"] = record["passed"] and record["order_exact"]
        if stage in ("power", "mel_energies", "frame_energy"):
            record["nonnegative"] = bool(np.all(a >= 0.0))
            record["passed"] = record["passed"] and record["nonnegative"]
        structural["arrays"][stage] = record
        structural["passed"] = structural["passed"] and record["passed"]
    if "frame_ids" in actual:
        ids = actual["frame_ids"]
        dtype_ok = ids.dtype.kind in "iu" and ids.dtype.itemsize == 8
        good_ids = ids.shape == (expected_count,) and np.array_equal(ids, np.arange(expected_count, dtype=np.uint64))
        structural["frame_ids_exact"] = bool(good_ids)
        structural["frame_ids_dtype_ok"] = bool(dtype_ok)
        structural["passed"] = structural["passed"] and good_ids and dtype_ok
    else:
        structural["frame_ids_exact"] = False
        structural["frame_ids_dtype_ok"] = False
        structural["passed"] = False
    report["structural"] = structural
    for stage in STAGE_ORDER:
        if stage in actual and stage in reference:
            atol, rtol = _tolerance(tolerances, stage)
            record = _metric(actual[stage], reference[stage], atol, rtol)
            record["passed"] = record["passed"] and structural["arrays"][stage]["passed"]
            report["stages"][stage] = record
            if not record["passed"] and report["first_failed_stage"] is None:
                report["first_failed_stage"] = stage
        elif report["first_failed_stage"] is None:
            report["first_failed_stage"] = stage
    if not structural["passed"] and report["first_failed_stage"] is None:
        report["first_failed_stage"] = "structure"
    if "mfcc" in actual and actual["mfcc"].shape == reference["mfcc"].shape:
        atol, rtol = _tolerance(tolerances, "mfcc")
        for coefficient in range(13):
            record = _metric(actual["mfcc"][:, coefficient], reference["mfcc"][:, coefficient], atol, rtol)
            record["coefficient"] = coefficient
            report["per_coefficient_mfcc"].append(record)
    report["passed"] = bool(structural["passed"] and all(value["passed"] for value in report["stages"].values()))
    return report
