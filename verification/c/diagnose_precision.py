"""Reproduce precision diagnostics for two fixed development synthetic inputs.

Float64 continuations isolate information already lost at a C stage boundary.
They are diagnostic calculations, not C implementation results or acceptance
tests. This module does not read evaluation inputs or change pass tolerances.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np
import scipy
from scipy.fft import rfft


CASES = ("fullscale_alternating", "tone_bin32_1000hz")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric(actual: np.ndarray, expected: np.ndarray,
            tolerance: dict[str, Any]) -> dict[str, Any]:
    if actual.shape != expected.shape or not actual.size:
        raise ValueError("Diagnostic stages require matching nonempty arrays")
    if not (np.isfinite(actual).all() and np.isfinite(expected).all()):
        raise ValueError("Diagnostic stages must be finite")
    error = np.abs(actual - expected)
    index = np.unravel_index(int(error.argmax()), error.shape)
    bad = error > tolerance["atol"] + tolerance["rtol"] * np.abs(expected)
    first_bad = np.argwhere(bad)
    return {
        "max_abs": float(error[index]),
        "rmse": float(np.sqrt(np.mean(error * error))),
        "max_error_index": [int(value) for value in index],
        "violations": int(bad.sum()),
        "first_violation_index": ([int(value) for value in first_bad[0]]
                                  if len(first_bad) else None),
    }


def diagnose(reference_root: Path | str, c_run: Path | str,
             out: Path | str) -> dict[str, Any]:
    """Write ``out/diagnosis.json`` and return the same report dictionary.

    Inputs are a pinned Python run and an existing C development run containing
    the two names in CASES. Nothing beneath either run's evaluation directory is
    accessed. All input files read by the diagnosis are recorded with SHA256.
    """
    reference_root = Path(reference_root).resolve()
    c_run = Path(c_run).resolve()
    out = Path(out).resolve()
    files_read: dict[str, str] = {}

    def read_json(path: Path) -> dict[str, Any]:
        files_read[str(path)] = _sha(path)
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def load(folder: Path, name: str = "arrays.json") -> dict[str, np.ndarray]:
        meta = read_json(folder / name)
        if meta.get("profile_id") != "comparison_raw13":
            raise ValueError(f"Expected comparison_raw13: {folder / name}")
        arrays = {}
        for key, desc in meta["arrays"].items():
            path = (folder / desc["file"]).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError(f"Descriptor escapes diagnostic directory: {key}")
            files_read[str(path)] = _sha(path)
            if desc.get("sha256") and files_read[str(path)] != desc["sha256"]:
                raise ValueError(f"Diagnostic input hash mismatch: {path}")
            dtype = np.dtype(desc["dtype"])
            shape = tuple(desc["shape"])
            if any(not isinstance(size, int) or size < 0 for size in shape):
                raise ValueError(f"Invalid diagnostic array shape: {key}")
            expected_bytes = int(np.prod(shape, dtype=np.int64)) * dtype.itemsize
            if path.stat().st_size != expected_bytes or desc["bytes"] != expected_bytes:
                raise ValueError(f"Diagnostic array byte count mismatch: {key}")
            arrays[key] = np.fromfile(path, dtype=dtype).reshape(shape)
        return arrays

    tables = load(reference_root / "coefficients" / "comparison_raw13")
    tolerances = read_json(c_run / "tolerances.json")
    if (tables["window"].shape != (512,)
            or tables["mel_filters"].shape != (26, 257)
            or tables["dct_matrix"].shape != (13, 26)):
        raise ValueError("Unexpected comparison_raw13 coefficient dimensions")

    def continuation(fft: np.ndarray) -> dict[str, np.ndarray]:
        power = (fft.real * fft.real + fft.imag * fft.imag) / 512.0
        mel = power @ tables["mel_filters"].T
        log_mel = np.log(np.maximum(mel, 1e-12))
        mfcc = log_mel @ tables["dct_matrix"].T
        return {"fft": fft, "power": power, "mel_energies": mel,
                "log_mel": log_mel, "mfcc": mfcc}

    result: dict[str, Any] = {
        "schema_version": 1,
        "diagnostic_only": True,
        "evaluation_accessed": False,
        "reference_root": str(reference_root),
        "c_run": str(c_run),
        "case_scope": list(CASES),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "comparison_tolerances_unchanged": tolerances,
        "cases": {},
    }
    for case in CASES:
        actual = load(c_run / "synthetic" / case, "host_manifest.json")
        reference = load(reference_root / "synthetic" / case / "comparison_raw13")
        validation = read_json(c_run / "synthetic" / case / "validation.json")
        if (reference["frames"].ndim != 2 or reference["frames"].shape[0] < 2
                or reference["frames"].shape[1] != 512
                or actual["frames"].shape != reference["frames"].shape):
            raise ValueError(f"Expected at least two complete 512-sample frames: {case}")
        c_window = tables["window"].astype(np.float32)
        variants = {
            "actual_C_fft_f64_downstream": actual["fft"].astype(np.complex128),
            "f64_fft_of_C_windowed": rfft(actual["windowed"].astype(np.float64)),
            "scipy_f32_fft_of_C_windowed": rfft(actual["windowed"]).astype(np.complex128),
            "f64_fft_C_preemphasis_ideal_window":
                rfft(actual["frames"].astype(np.float64) * tables["window"]),
            "f64_fft_ref_preemphasis_C_window":
                rfft(reference["frames"] * c_window.astype(np.float64)),
            "f64_fft_ideal_preemphasis_rounded_only":
                rfft(reference["frames"].astype(np.float32).astype(np.float64)
                     * tables["window"]),
            "f64_fft_ideal_preemphasis_rounded_C_window_product":
                rfft((reference["frames"].astype(np.float32) * c_window).astype(np.float64)),
            "f64_fft_ideal_complete_windowed_rounded":
                rfft(reference["windowed"].astype(np.float32).astype(np.float64)),
        }
        diagnostic: dict[str, Any] = {
            "original_stages": validation["stages"],
            "variants": {},
            "C_preemphasis_equals_correctly_rounded_float64": bool(np.array_equal(
                actual["preemphasis"], reference["preemphasis"].astype(np.float32))),
            "frame1_mel_energies_reference": reference["mel_energies"][1].tolist(),
            "frame1_mel_energies_C": actual["mel_energies"][1].tolist(),
            "frame1_mel_energies_f64_fft_C_windowed":
                continuation(variants["f64_fft_of_C_windowed"])["mel_energies"][1].tolist(),
        }
        for name, fft in variants.items():
            stages = continuation(fft)
            diagnostic["variants"][name] = {
                stage: _metric(value, reference[stage],
                               tolerances["stages"].get(stage, tolerances["default"]))
                for stage, value in stages.items()
            }
        if case == "fullscale_alternating":
            ref_unique = np.unique(reference["frames"][1])
            c_unique = np.unique(actual["frames"][1]).astype(np.float64)
            diagnostic["frame1_preemphasis_unique_reference"] = ref_unique.tolist()
            diagnostic["frame1_preemphasis_unique_C"] = c_unique.tolist()
            diagnostic["frame1_reference_DC_pair_sum"] = float(ref_unique.sum())
            diagnostic["frame1_C_DC_pair_sum"] = float(c_unique.sum())
            fft_error = np.abs(actual["fft"].astype(np.complex128) - reference["fft"])
            fft_tolerance = tolerances["stages"]["fft"]
            bad = fft_error > (fft_tolerance["atol"]
                               + fft_tolerance["rtol"] * np.abs(reference["fft"]))
            diagnostic["failed_fft_elements"] = [{
                "index": [int(value) for value in index],
                "actual_re_im": [float(actual["fft"][tuple(index)].real),
                                 float(actual["fft"][tuple(index)].imag)],
                "reference_re_im": [float(reference["fft"][tuple(index)].real),
                                    float(reference["fft"][tuple(index)].imag)],
            } for index in np.argwhere(bad)]
        result["cases"][case] = diagnostic
    result["files_read_sha256"] = files_read
    result["script_sha256"] = _sha(Path(__file__).resolve())
    out.mkdir(parents=True, exist_ok=True)
    (out / "diagnosis.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--c-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.reference_root, args.c_run, args.output_dir)
    print(args.output_dir.resolve() / "diagnosis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
