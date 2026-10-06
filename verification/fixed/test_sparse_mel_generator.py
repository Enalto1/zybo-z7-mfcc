#!/usr/bin/env python3
"""Exercise generation/check CLI and preserve detected mutation failures.

Run with --run-dir for permanent command logs, exit codes and test artifacts.
The published contract and project coefficient files are only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate_sparse_mel.py"
ARTIFACTS = ("fixed_mel_descriptor.sv", "mel_sparse_fw16.mem")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_checks(run_dir):
    commands = []

    def command(name, arguments, expected_code, diagnostic=None):
        argv = [sys.executable, "-B", str(SCRIPT), *map(str, arguments)]
        result = subprocess.run(argv, capture_output=True, text=True)
        (run_dir / (name + ".stdout.log")).write_bytes(result.stdout.encode("utf-8"))
        (run_dir / (name + ".stderr.log")).write_bytes(result.stderr.encode("utf-8"))
        commands.append(dict(name=name, argv=argv, exit_code=result.returncode,
                             expected_exit_code=expected_code))
        if result.returncode != expected_code:
            raise AssertionError(f"{name}: exit {result.returncode}, expected {expected_code}: {result.stderr}")
        if diagnostic is not None and diagnostic not in result.stderr:
            raise AssertionError(f"{name}: expected diagnostic {diagnostic!r}")

    first, second = run_dir / "first", run_dir / "second"
    command("generate_first", ["--output-dir", first], 0)
    command("generate_second", ["--output-dir", second], 0)
    for name in ARTIFACTS:
        if (first / name).read_bytes() != (second / name).read_bytes():
            raise AssertionError("independent generation differs: " + name)
        if b"\r" in (first / name).read_bytes():
            raise AssertionError("output is not LF-only")
    before = {name: (sha(first / name), (first / name).stat().st_mtime_ns) for name in ARTIFACTS}
    command("check_first", ["--check", "--output-dir", first], 0)
    after = {name: (sha(first / name), (first / name).stat().st_mtime_ns) for name in ARTIFACTS}
    if before != after:
        raise AssertionError("check modified output")
    command("check_project", ["--check"], 0)
    for mutation, filename, old, new in (
        ("length", "fixed_mel_descriptor.sv", b"o_length = 9'd3;", b"o_length = 9'd2;"),
        ("offset", "fixed_mel_descriptor.sv", b"o_offset = 9'd3;", b"o_offset = 9'd4;"),
        ("coefficient", "mel_sparse_fw16.mem", b"08000\n", b"08001\n"),
    ):
        target = run_dir / ("mutation_" + mutation)
        shutil.copytree(first, target)
        path = target / filename
        original = path.read_bytes()
        if old not in original:
            raise AssertionError("mutation failed to match: " + mutation)
        path.write_bytes(original.replace(old, new, 1))
        command("reject_" + mutation, ["--check", "--output-dir", target], 1,
                "generated artifact mismatch: " + filename)
    dense = ROOT / "hardware/fixed/power_mel/mel_fw16.mem"
    altered_dense = run_dir / "mutated_dense.mem"
    altered_dense.write_bytes(dense.read_bytes().replace(b"00000", b"00001", 1))
    command("reject_dense_source", ["--check", "--output-dir", first, "--dense-rom", altered_dense],
            1, "dense ROM active coefficient mismatch")
    (run_dir / "test_sparse_mel_generator.py").write_bytes(Path(__file__).read_bytes())
    report = dict(status="PASS", independent_generations_byte_identical=True,
                  check_preserves_bytes_and_mtime=True, lf_only=True,
                  detected_artifact_mutations=3, detected_dense_mutations=1,
                  artifact_sha256={name: sha(first / name) for name in ARTIFACTS},
                  generator_sha256=sha(SCRIPT), test_sha256=sha(Path(__file__)), commands=commands)
    (run_dir / "verification.json").write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, help="new directory for preserved test evidence")
    args = parser.parse_args()
    if args.run_dir:
        args.run_dir.mkdir(parents=True, exist_ok=False)
        run_checks(args.run_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="mfcc_sparse_generator_") as temporary:
            run_checks(Path(temporary))


if __name__ == "__main__":
    main()
