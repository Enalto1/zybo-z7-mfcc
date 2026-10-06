#!/usr/bin/env python3
"""Fail-closed reference/input checks using only temporary synthetic fixtures.

No frozen reference, development speech, or evaluation speech is loaded.
Run: python verification/fixed/test_fft_precision_inputs.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import run_fft_precision_study as study  # noqa: E402

CHECKS = 0
FAILS = []


def check(label, fn, *args, **kwargs):
    global CHECKS
    CHECKS += 1
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        FAILS.append(f"{label}: {type(exc).__name__}: {exc}")


def equal(got, want):
    if got != want:
        raise AssertionError(f"got {got!r}; expected {want!r}")


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def write_fixture(directory, data=None):
    directory.mkdir()
    if data is None:
        data = np.arange(6, dtype="<f8").reshape(2, 3)
    payload = data.tobytes(order="C")
    (directory / "probe.bin").write_bytes(payload)
    meta = {"arrays": {"probe": {
        "file": "probe.bin", "dtype": data.dtype.str,
        "shape": list(data.shape), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest()}}}
    write_metadata(directory, meta)
    return meta


def write_metadata(directory, metadata):
    (directory / "arrays.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_loader(base):
    directory = base / "valid"
    write_fixture(directory)
    arrays, audit = study.load_checked_arrays(directory, {"probe": (2, 3)})
    check("valid array exact values", np.testing.assert_array_equal,
          arrays["probe"], np.arange(6, dtype=float).reshape(2, 3))
    digest = hashlib.sha256((directory / "probe.bin").read_bytes()).hexdigest()
    check("actual digest recorded", equal,
          audit["arrays"]["probe"]["actual_sha256"], digest)
    check("metadata digest recorded", equal, audit["metadata_sha256"],
          hashlib.sha256((directory / "arrays.json").read_bytes()).hexdigest())
    check("required missing array rejected", raises, RuntimeError,
          study.load_checked_arrays, directory, {"missing": (2, 3)})
    check("required wrong shape rejected", raises, RuntimeError,
          study.load_checked_arrays, directory, {"probe": (3, 2)})

    for name, field, value in (
            ("wrong_hash", "sha256", "f" * 64),
            ("missing_hash", "sha256", None),
            ("wrong_bytes", "bytes", 49),
            ("missing_bytes", "bytes", None),
            ("wrong_shape", "shape", [2, 4]),
            ("unsupported_dtype", "dtype", "<f4")):
        directory = base / name
        meta = write_fixture(directory)
        if value is None:
            del meta["arrays"]["probe"][field]
        else:
            meta["arrays"]["probe"][field] = value
        write_metadata(directory, meta)
        check(f"{name} rejected", raises, RuntimeError,
              study.load_checked_arrays, directory)

    # The hash is refreshed, so byte-count protection must catch truncation
    # and appended junk independently of SHA validation.
    for name, payload in (("truncated_file", b"\0" * 47),
                          ("appended_file", b"\0" * 49)):
        directory = base / name
        meta = write_fixture(directory)
        (directory / "probe.bin").write_bytes(payload)
        meta["arrays"]["probe"]["sha256"] = hashlib.sha256(payload).hexdigest()
        write_metadata(directory, meta)
        check(f"{name} rejected after valid hash", raises, RuntimeError,
              study.load_checked_arrays, directory)

    for index, value in enumerate((np.nan, np.inf, -np.inf,
                                  complex(1, np.nan), complex(np.inf, 2))):
        directory = base / f"nonfinite_{index}"
        write_fixture(directory, np.asarray([value]))
        check(f"nonfinite payload {value!r} rejected", raises, RuntimeError,
              study.load_checked_arrays, directory)

    for name, data in (("empty", np.empty((0, 257), dtype="<f8")),
                        ("complex", np.asarray([1 + 2j, -3j], dtype="<c16")),
                        ("integer", np.asarray([-2, 0, 7], dtype="<i8"))):
        directory = base / name
        write_fixture(directory, data)
        loaded, _ = study.load_checked_arrays(directory, {"probe": data.shape})
        check(f"valid {name} payload", np.testing.assert_array_equal,
              loaded["probe"], data)


def case_arrays(samples):
    nf = 0 if samples < 512 else 1 + (samples - 512) // 160
    shapes = {"input_float": (samples,), "preemphasis": (samples,),
              "frame_starts": (nf,), "frames": (nf, 512),
              "windowed": (nf, 512), "fft": (nf, 257),
              "power": (nf, 257), "mel_energies": (nf, 26),
              "log_mel": (nf, 26), "mfcc": (nf, 13)}
    return {name: np.zeros(shape, dtype=("<i8" if name == "frame_starts"
             else "<c16" if name == "fft" else "<f8"))
            for name, shape in shapes.items()}


def test_case_validation():
    for samples in (0, 511, 512, 671, 672, 832):
        check(f"valid full-frame boundary {samples}", study.validate_case_arrays,
              case_arrays(samples), samples)
    for name in study.REQUIRED_ARRAYS:
        arr = case_arrays(672)
        del arr[name]
        check(f"missing required {name}", raises, RuntimeError,
              study.validate_case_arrays, arr, 672)
        arr = case_arrays(672)
        arr[name] = arr[name][:-1]
        check(f"wrong shape {name}", raises, RuntimeError,
              study.validate_case_arrays, arr, 672)
        arr = case_arrays(672)
        arr[name] = arr[name].astype("<i4" if name == "frame_starts"
                                   else "<c8" if name == "fft" else "<f4")
        check(f"wrong dtype {name}", raises, RuntimeError,
              study.validate_case_arrays, arr, 672)
        if name != "frame_starts":
            for invalid in (np.nan, np.inf, -np.inf):
                arr = case_arrays(672)
                arr[name].flat[0] = invalid
                check(f"nonfinite {name} {invalid!r} rejected", raises,
                      ValueError, study.validate_case_arrays, arr, 672)
    for name in ("power", "mel_energies"):
        arr = case_arrays(672)
        arr[name][0, 0] = -1e-100
        check(f"negative {name} rejected", raises, ValueError,
              study.validate_case_arrays, arr, 672)
    for invalid in (np.nan, np.inf, -np.inf):
        arr = case_arrays(672)
        arr["mel_energies"][0, 0] = invalid
        check(f"invalid generated Mel {invalid!r}", raises, ValueError,
              study.validate_case_arrays, arr, 672)


def test_integer_storage(base):
    path = base / "empty_integer.bin"
    info = study.save_int_array(path, [], columns=257)
    check("empty integer shape keeps column count", equal, info["shape"], [0, 257])
    check("empty integer dump has zero bytes", equal, path.stat().st_size, 0)
    check("empty integer hash", equal, info["sha256"], hashlib.sha256(b"").hexdigest())
    check("integer dump refuses ragged rows", raises, ValueError,
          study.save_int_array, base / "ragged.bin", [[1, 2], [3]])
    check("integer dump refuses float truncation", raises, TypeError,
          study.save_int_array, base / "float.bin", [[1.5]])
    check("integer dump refuses supplied column mismatch", raises, ValueError,
          study.save_int_array, base / "wrong_columns.bin", [[1, 2]], columns=257)


def test_count_and_cli(base):
    reference = base / "reference"
    reference.mkdir()
    out = base / "output"
    out.mkdir()
    # No real coefficients are needed before the empty-set count check.
    coeff = {"window": np.zeros(1), "mel_filters": np.zeros((1, 1)),
             "dct_matrix": np.zeros((1, 1))}
    check("missing synthetic input directory rejected", raises, FileNotFoundError,
          study.collect_cases, reference, {"synthetic"}, out, {}, coeff)
    (reference / "synthetic").mkdir()
    check("zero synthetic inputs cannot masquerade as 17", raises, RuntimeError,
          study.collect_cases, reference, {"synthetic"}, out, {}, coeff)

    command = [sys.executable, "-B", str(ROOT / "scripts/run_fft_precision_study.py"),
               "--out-root", str(out), "--reference-run", str(reference)]
    result = subprocess.run(command + ["--run-id", "evaluation_rejected", "--inputs", "evaluation"],
                            capture_output=True, text=True, timeout=30)
    check("CLI evaluation inputs rejected", equal, result.returncode, 2)
    check("CLI explains evaluation prohibition", equal,
          "evaluation prohibited" in result.stderr, True)
    check("CLI invalid input creates no run folder", equal,
          (out / "evaluation_rejected").exists(), False)

    existing = out / "existing"
    existing.mkdir()
    marker = existing / "original.txt"
    marker.write_bytes(b"immutable previous experiment\n")
    before = marker.read_bytes()
    result = subprocess.run(command + ["--run-id", "existing"],
                            capture_output=True, text=True, timeout=30)
    check("CLI existing run rejected", equal, result.returncode, 1)
    check("CLI explains existing directory refusal", equal,
          "already exists" in result.stdout, True)
    check("existing file content preserved", equal, marker.read_bytes(), before)
    check("existing directory has no new outputs", equal,
          sorted(p.name for p in existing.iterdir()), ["original.txt"])


def main():
    with tempfile.TemporaryDirectory(prefix="mfcc_precision_input_tests_") as directory:
        base = Path(directory)
        test_loader(base)
        test_case_validation()
        test_integer_storage(base)
        test_count_and_cli(base)
    print(f"test_fft_precision_inputs: {CHECKS} checks, {len(FAILS)} failures")
    for failure in FAILS:
        print("  FAIL", failure)
    print("RESULT:", "FAIL" if FAILS else "PASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
