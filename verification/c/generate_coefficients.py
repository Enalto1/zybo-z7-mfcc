"""Export binary32 C tables from a completed, pinned Python reference run.

Saved float64 coefficient tables are converted rather than regenerated. Only
the original radix-2 FFT's 256 twiddles are newly generated here. This module
does not read evaluation audio or evaluation stage results.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


# This is the complete pinned algorithm contract, not a set of guessed package
# defaults. Unknown/new profile keys must be reviewed before generating C tables.
MAIN_PROFILE_CONTRACT: dict[str, Any] = {
    "schema_version": 1,
    "profile_id": "comparison_raw13",
    "spec_id": "mfcc-raw13-v0.1-draft",
    "sample_rate": 16000,
    "frame_length": 512,
    "frame_step": 160,
    "nfft": 512,
    "num_filters": 26,
    "num_ceps": 13,
    "pcm_divisor": 32768.0,
    "preemphasis": 0.95,
    "initial_previous_sample": 0.0,
    "frame_policy": "full_frames_only",
    "empty_input_policy": "zero_frames",
    "window": "symmetric_hamming",
    "fft_norm": "backward",
    "fft_sign": "negative",
    "power_divisor": 512,
    "one_sided_double": False,
    "mel_scale": "htk",
    "lowfreq": 0.0,
    "highfreq": 8000.0,
    "mel_bin_rule": "floor((nfft+1)*hz/sample_rate)",
    "mel_normalization": "peak_one_no_area_normalization",
    "log_base": "natural",
    "log_policy": "floor",
    "log_floor": 1e-12,
    "dct_type": 2,
    "dct_norm": "ortho",
    "lifter": 0,
    "append_energy": False,
    "delta": False,
    "delta_delta": False,
    "cmvn": False,
    "output_order": [f"C{index}" for index in range(13)],
    "arithmetic_dtype": "float64",
}


def _validate_main_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise ValueError("Pinned main profile must be a JSON object")
    allowed = set(MAIN_PROFILE_CONTRACT) | {"provenance"}
    if set(profile) != allowed:
        raise ValueError(f"Pinned main profile keys differ: missing={sorted(allowed - set(profile))}, extra={sorted(set(profile) - allowed)}")
    if not isinstance(profile["provenance"], dict):
        raise ValueError("Pinned main profile provenance must be a JSON object")
    for key, expected in MAIN_PROFILE_CONTRACT.items():
        value = profile[key]
        # bool is a subclass of int in Python; reject 0 in place of false and
        # true in place of 1 instead of silently accepting a type-changed schema.
        if type(value) is not type(expected) or value != expected:
            raise ValueError(f"Unsupported main profile {key}={value!r}; expected {expected!r} ({type(expected).__name__})")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _dump(path: Path, content: Any) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _checked_source(root: Path, relative: str, artifacts: dict[str, str]) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"Source path escapes reference root: {relative}")
    if relative not in artifacts or _sha(candidate) != artifacts[relative]:
        raise ValueError(f"Pinned artifact hash mismatch: {relative}")
    return candidate


def _hex(value: Any) -> str:
    # Python float exactly represents every finite binary32 value. Its hex
    # spelling therefore transfers that value exactly to a C99 float literal.
    return float(value).hex() + "f"


def _declaration(symbol: str, array: np.ndarray, external: bool) -> str:
    dims = "".join(f"[{size}]" for size in array.shape)
    prefix = "extern " if external else ""
    if external:
        return f"{prefix}const float {symbol}{dims};"
    if array.ndim == 0:
        body = _hex(array.item())
    elif array.ndim == 1:
        lines = [", ".join(_hex(value) for value in array[start:start + 8])
                 for start in range(0, len(array), 8)]
        body = "{\n    " + ",\n    ".join(lines) + "\n}"
    elif array.ndim == 2:
        rows = []
        for row in array:
            lines = [", ".join(_hex(value) for value in row[start:start + 8])
                     for start in range(0, len(row), 8)]
            rows.append("    {\n        " + ",\n        ".join(lines) + "\n    }")
        body = "{\n" + ",\n".join(rows) + "\n}"
    else:
        raise ValueError("Only scalar, one- and two-dimensional C tables are supported")
    return f"const float {symbol}{dims} = {body};"


def generate(reference_root: Path, out: Path) -> dict[str, Any]:
    """Verify used source artifacts and write .bin, arrays.json and C tables.

    The returned manifest lists every source/coefficient hash and the generator
    hash. Output must be empty to avoid silently overwriting an earlier run.
    """
    root, out = Path(reference_root).resolve(), Path(out).resolve()
    if out == root or out.is_relative_to(root):
        raise ValueError("Generated C coefficients must be outside the pinned Python run")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Coefficient output must be empty: {out}")
    artifacts = _read(root / "artifact_manifest.json")
    run_path = _checked_source(root, "run_manifest.json", artifacts)
    run = _read(run_path)
    if run["status"] != "passed":
        raise ValueError("Python reference run did not pass")
    freeze_path = _checked_source(root, "freeze.json", artifacts)
    if _sha(freeze_path) != run["freeze_sha256"]:
        raise ValueError("Reference freeze hash does not match run manifest")
    profile_relative = "profiles/comparison_raw13.json"
    profile_path = _checked_source(root, profile_relative, artifacts)
    profile = _read(profile_path)
    _validate_main_profile(profile)
    # Confirm the environment's IEEE conversion mode on halfway probes.
    midpoint = np.array([1.0 + 2.0**-24, 1.0 + 3.0 * 2.0**-24,
                         -1.0 - 2.0**-24], dtype="<f8")
    if not np.array_equal(midpoint.astype("<f4"), np.array([1.0, 1.0 + 2.0**-22, -1.0], dtype="<f4")):
        raise RuntimeError("binary64-to-binary32 ties-to-even probes failed")
    schema_relative = "coefficients/comparison_raw13/arrays.json"
    schema_path = _checked_source(root, schema_relative, artifacts)
    schema = _read(schema_path)
    expected_shapes = {"window": [512], "mel_edges": [28], "mel_filters": [26, 257],
                       "dct_cosine": [13, 26], "dct_scale": [13]}
    converted: dict[str, np.ndarray] = {}
    source_records: dict[str, Any] = {}
    used_sources = {"run_manifest.json": _sha(run_path), "freeze.json": _sha(freeze_path),
                    profile_relative: _sha(profile_path), schema_relative: _sha(schema_path)}
    for name, shape in expected_shapes.items():
        descriptor = schema["arrays"][name]
        relative = "coefficients/comparison_raw13/" + descriptor["file"]
        path = _checked_source(root, relative, artifacts)
        expected_dtype = "<i8" if name == "mel_edges" else "<f8"
        if descriptor["shape"] != shape or descriptor["dtype"] != expected_dtype:
            raise ValueError(f"Unexpected pinned table shape/dtype: {name}")
        if _sha(path) != descriptor["sha256"] or path.stat().st_size != descriptor["bytes"]:
            raise ValueError(f"Coefficient source descriptor mismatch: {name}")
        source = np.fromfile(path, dtype=expected_dtype).reshape(shape)
        if not np.isfinite(source).all():
            raise ValueError(f"Nonfinite source coefficient: {name}")
        target_dtype = "<i8" if name == "mel_edges" else "<f4"
        converted[name] = np.ascontiguousarray(source, dtype=target_dtype)
        used_sources[relative] = _sha(path)
        source_records[name] = {"source_file": relative, "source_sha256": _sha(path),
            "source_dtype": expected_dtype, "target_dtype": target_dtype,
            "conversion": "integer preserved" if name == "mel_edges" else "NumPy astype('<f4'), IEEE754 round-to-nearest ties-to-even",
            "max_abs_rounding_error": float(np.max(np.abs(converted[name].astype(np.float64) - source)))}
    twiddle = np.empty(256, dtype=np.complex128)
    for index in range(256):
        if index == 0:
            twiddle[index] = complex(1.0, -0.0)
        elif index == 128:
            twiddle[index] = complex(0.0, -1.0)
        else:
            angle = -2.0 * math.pi * index / 512.0
            twiddle[index] = complex(math.cos(angle), math.sin(angle))
    converted["twiddle_re"] = np.ascontiguousarray(twiddle.real, dtype="<f4")
    converted["twiddle_im"] = np.ascontiguousarray(twiddle.imag, dtype="<f4")
    converted["preemphasis"] = np.array(profile["preemphasis"], dtype="<f4")
    converted["log_floor"] = np.array(profile["log_floor"], dtype="<f4")
    out.mkdir(parents=True, exist_ok=True)
    output_schema: dict[str, Any] = {"profile_id": "comparison_raw13", "storage": "headerless little-endian, C row-major", "arrays": {}}
    for name, array in converted.items():
        target = out / f"{name}.bin"
        array.tofile(target)
        output_schema["arrays"][name] = {"file": target.name, "shape": list(array.shape),
            "dtype": array.dtype.str, "bytes": target.stat().st_size, "sha256": _sha(target),
            "finite": bool(np.isfinite(array).all()), "active_in_core": name != "mel_edges"}
    _dump(out / "arrays.json", output_schema)
    names = {"window": "mfcc_window", "mel_filters": "mfcc_mel",
             "dct_cosine": "mfcc_dct_cosine", "dct_scale": "mfcc_dct_scale",
             "twiddle_re": "mfcc_twiddle_re", "twiddle_im": "mfcc_twiddle_im",
             "preemphasis": "mfcc_preemphasis", "log_floor": "mfcc_log_floor"}
    header = ["/* Generated from pinned Python float64 tables. Do not edit. */",
              "#ifndef MFCC_TABLES_H", "#define MFCC_TABLES_H", ""]
    source_lines = ["/* Generated exact binary32 C99 hexadecimal literals. */", '#include "mfcc_tables.h"', ""]
    for name, symbol in names.items():
        header.append(_declaration(symbol, converted[name], True))
        source_lines.append(_declaration(symbol, converted[name], False))
        source_lines.append("")
    header += ["", "#endif", ""]
    (out / "mfcc_tables.h").write_text("\n".join(header), encoding="ascii")
    (out / "mfcc_tables.c").write_text("\n".join(source_lines), encoding="ascii")
    manifest: dict[str, Any] = {
        "schema_version": 1, "reference_root": str(root), "profile_id": "comparison_raw13",
        "validated_profile_contract": MAIN_PROFILE_CONTRACT,
        "source_artifact_manifest_sha256": _sha(root / "artifact_manifest.json"),
        "verified_used_source_artifacts": used_sources,
        "generator": str(Path(__file__).resolve()), "generator_sha256": _sha(Path(__file__)),
        "numpy_version": np.__version__, "tables": source_records,
        "twiddle_generation": {"formula": "cos(-2*pi*k/512) + j*sin(-2*pi*k/512), k=0..255",
            "computation_dtype": "binary64 Python math", "rounding": "IEEE754 binary32 round-to-nearest ties-to-even",
            "exact_cardinals": {"0": [1.0, -0.0], "128": [0.0, -1.0]},
            "float64_real_sha256": hashlib.sha256(np.ascontiguousarray(twiddle.real, dtype="<f8").tobytes()).hexdigest(),
            "float64_imag_sha256": hashlib.sha256(np.ascontiguousarray(twiddle.imag, dtype="<f8").tobytes()).hexdigest()},
        "scalar_sources": {name: {"profile_value_binary64": float(profile[name]),
            "binary32_value": float(converted[name]), "c99_hex_literal": _hex(converted[name])}
            for name in ("preemphasis", "log_floor")},
        "dct_contract": "sum log_mel[m]*mfcc_dct_cosine[c][m] ascending m, then multiply mfcc_dct_scale[c] once; dct_matrix not compiled into C",
        "output_hashes": {p.name: _sha(p) for p in sorted(out.iterdir()) if p.is_file()},
    }
    _dump(out / "coefficient_manifest.json", manifest)
    return manifest
