"""Exercise the C file adapter independently of numerical MFCC tolerances.

Only the pinned development clip is used. Evaluation audio is never opened.
All subprocess inputs, logs, manifests and comparisons remain in the run folder.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


STAGES = {
    "input_float": ("<f4", None),
    "preemphasis": ("<f4", None),
    "frame_starts": ("<u8", 0),
    "frame_ids": ("<u8", 0),
    "frames": ("<f4", 512),
    "windowed": ("<f4", 512),
    "fft": ("<c8", 257),
    "power": ("<f4", 257),
    "mel_energies": ("<f4", 26),
    "log_mel": ("<f4", 26),
    "dct": ("<f4", 13),
    "mfcc": ("<f4", 13),
    "frame_energy": ("<f4", 0),
}


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _run(binary: Path, pcm: Path, output: Path, chunk: int, log: Path) -> dict[str, Any]:
    command = [str(binary), "--input", str(pcm), "--output", str(output), "--chunk-size", str(chunk)]
    record: dict[str, Any] = {"command_argv": command, "timeout_seconds": 60, "timed_out": False}
    try:
        process = subprocess.run(command, cwd=log.parent, capture_output=True, text=True, errors="replace", timeout=60)
        record.update(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
    except subprocess.TimeoutExpired as error:
        def text(value: str | bytes | None) -> str:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        record.update(exit_code=None, timed_out=True, stdout=text(error.stdout), stderr=text(error.stderr))
    _write(log, record)
    return record


def _successful_arrays(folder: Path, sample_count: int) -> dict[str, Any]:
    """Check descriptors/file sizes independently of the host implementation."""
    metadata = _read(folder / "host_manifest.json")
    frames = max(0, 1 + (sample_count - 512) // 160)
    failures = []
    if metadata.get("status") != "passed" or metadata.get("error") is not None:
        failures.append("host did not report success")
    if metadata.get("profile_id") != "comparison_raw13":
        failures.append("unexpected profile")
    if metadata.get("sample_count") != sample_count or metadata.get("frame_count") != frames:
        failures.append("sample/frame count mismatch")
    if any(metadata.get("error_counts", {}).values()):
        failures.append("nonzero host error counters")
    arrays = metadata.get("arrays", {})
    if set(arrays) != set(STAGES):
        failures.append("stage set mismatch")
    hashes = {}
    for name, (dtype, width) in STAGES.items():
        descriptor = arrays.get(name)
        if not isinstance(descriptor, dict):
            failures.append(f"{name}: missing descriptor")
            continue
        expected_shape = [sample_count] if width is None else [frames] if width == 0 else [frames, width]
        size = (sample_count if width is None else frames * max(width, 1)) * (8 if dtype in ("<u8", "<c8") else 4)
        path = folder / f"{name}.bin"
        if descriptor.get("file") != path.name or descriptor.get("dtype") != dtype or descriptor.get("shape") != expected_shape:
            failures.append(f"{name}: descriptor mismatch")
        if descriptor.get("bytes") != size or not path.is_file() or path.stat().st_size != size:
            failures.append(f"{name}: byte count mismatch")
        if path.is_file():
            hashes[name] = {"sha256": _sha(path), "bytes": path.stat().st_size}
    return {"passed": not failures, "failures": failures, "sample_count": sample_count, "frame_count": frames, "arrays": hashes}


def run_host_checks(binary: str | Path, out: str | Path, reference_root: str | Path) -> dict[str, Any]:
    """Run chunk invariance, odd-byte rejection, bad-output and empty-input tests.

    ``out`` is the current C run root. A new ``verification/host_checks`` folder
    is required to prevent replacing earlier evidence. A returned false gate
    must prevent freezing/evaluation. Invalid reference provenance raises.
    """
    binary, reference = Path(binary).resolve(), Path(reference_root).resolve()
    root = Path(out).resolve() / "verification" / "host_checks"
    root.mkdir(parents=True, exist_ok=False)
    artifacts = _read(reference / "artifact_manifest.json")

    def pinned(relative: Path) -> Path:
        path = reference / relative
        if not path.is_relative_to(reference) or artifacts.get(relative.as_posix()) != _sha(path):
            raise ValueError(f"Pinned development artifact mismatch: {relative}")
        return path

    dataset = _read(pinned(Path("dataset_manifest_snapshot.json")))
    development = [clip for clip in dataset["clips"] if clip["role"] == "development"]
    if len(development) != 1:
        raise ValueError("Exactly one pinned development clip is required")
    clip_id = development[0]["utterance_id"]
    case_path = Path("development") / clip_id
    validated = _read(pinned(case_path / "validation.json"))
    pcm = pinned(case_path / "input_s16le.pcm")
    sample_count = validated["sample_count"]
    input_hash = _sha(pcm)
    if validated.get("passed") is not True or input_hash != validated["pcm_sha256"] or pcm.stat().st_size != sample_count * 2:
        raise ValueError("Development PCM does not match its successful Python validation")
    binary_hash = _sha(binary)
    checks: dict[str, Any] = {}

    runs = {}
    for chunk in (1, 4096):
        folder = root / f"chunk_{chunk}"
        folder.mkdir()
        process = _run(binary, pcm, folder, chunk, folder / "process.json")
        validated_run = _successful_arrays(folder, sample_count) if process["exit_code"] == 0 else {"passed": False, "arrays": {}}
        runs[chunk] = {"process": process, "validation": validated_run}
    comparison = {}
    for name in STAGES:
        left, right = runs[1]["validation"]["arrays"].get(name), runs[4096]["validation"]["arrays"].get(name)
        comparison[name] = {"chunk1": left, "chunk4096": right, "identical": left is not None and left == right}
    checks["chunk_invariance"] = {
        "passed": all(run["validation"]["passed"] for run in runs.values()) and all(row["identical"] for row in comparison.values()),
        "sample_count": sample_count, "clip_id": clip_id, "arrays": comparison,
        "runs": {str(chunk): run for chunk, run in runs.items()},
    }

    odd_pcm = root / "odd_byte_input.pcm"
    odd_pcm.write_bytes(b"\x01")
    odd_output = root / "odd_byte_output"
    odd_output.mkdir()
    odd_process = _run(binary, odd_pcm, odd_output, 4096, odd_output / "process.json")
    odd_metadata = _read(odd_output / "host_manifest.json") if (odd_output / "host_manifest.json").exists() else {}
    checks["odd_byte_rejected"] = {
        "passed": odd_process["exit_code"] not in (None, 0) and odd_metadata.get("status") == "failed"
        and "odd byte" in str(odd_metadata.get("error", "")).lower(),
        "process": odd_process, "manifest": odd_metadata,
    }

    blocked = root / "not_a_directory"
    blocked.write_text("Intentional regular file: host output children must fail.\n", encoding="utf-8")
    bad_process = _run(binary, pcm, blocked / "child", 4096, root / "bad_output_process.json")
    checks["bad_output_rejected"] = {
        "passed": bad_process["exit_code"] not in (None, 0)
        and "Could not open stage output" in bad_process["stderr"] and blocked.is_file(),
        "process": bad_process,
    }

    empty_pcm = root / "empty_input.pcm"
    empty_pcm.write_bytes(b"")
    empty_output = root / "empty_output"
    empty_output.mkdir()
    empty_process = _run(binary, empty_pcm, empty_output, 1, empty_output / "process.json")
    empty_validation = _successful_arrays(empty_output, 0) if empty_process["exit_code"] == 0 else {"passed": False}
    checks["empty_input"] = {"passed": empty_validation["passed"], "process": empty_process, "validation": empty_validation}
    unchanged = _sha(pcm) == input_hash and _sha(binary) == binary_hash
    result = {
        "passed": all(check["passed"] for check in checks.values()) and unchanged,
        "output": str(root), "binary": str(binary), "binary_sha256": binary_hash,
        "development_pcm": str(pcm), "development_pcm_sha256": input_hash,
        "inputs_and_binary_unchanged": unchanged, "checks": checks,
        "scope": "Host adapter contracts on development/synthetic inputs; no evaluation audio or performance measurement",
    }
    _write(root / "results.json", result)
    return result
