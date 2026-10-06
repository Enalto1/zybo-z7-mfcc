"""Read-only v2 final audit: hashes, actual ARM ELF bytes, and preservation.

Write a new report only. Does not run ARM, edit contracts, or use evaluation PCM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
import numpy as np

PROJECT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def index_check(root, index):
    hashes = read(index)
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError("Hash mismatch: " + str(path))
    return len(hashes)


def elf_bytes(path, address, length):
    data = path.read_bytes()
    if data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("Expected little-endian ELF32")
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if header[1] != 40:
        raise ValueError("Expected ARM machine")
    for i in range(header[9]):
        kind, offset, vaddr, _, filesz, _, _, _ = struct.unpack_from(
            "<IIIIIIII", data, header[4] + i * header[8])
        if kind == 1 and vaddr <= address and address + length <= vaddr + filesz:
            start = offset + address - vaddr
            return data[start:start + length]
    raise ValueError("Symbol not wholly within a load segment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--arm-id", default="arm_full_02")
    parser.add_argument("--boundary-id", default="published_boundaries_01")
    parser.add_argument("--math-run", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--v1-run", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    report = {"status": "failed", "auditor_sha256": sha(Path(__file__)),
              "run": str(run), "board_executed": False}
    try:
        report["pc_artifacts_checked"] = index_check(run, run / "pc_artifact_hashes.json")
        report["math_artifacts_checked"] = index_check(args.math_run, args.math_run / "artifact_hashes.json")
        report["v1_pc_artifacts_unchanged"] = index_check(args.v1_run, args.v1_run / "pc_artifact_hashes.json")
        v1_arm = args.v1_run / "arm_02"
        report["v1_arm_artifacts_unchanged"] = index_check(v1_arm, v1_arm / "artifact_manifest.json")
        before = read(args.before)
        for relative, expected in before.items():
            if sha(PROJECT / relative) != expected:
                raise ValueError("Prior source changed: " + relative)
        report["prior_source_files_unchanged"] = len(before)
        pc = read(run / "pc_results.json")
        math = read(args.math_run / "math_report.json")
        boundary_folder = run / args.boundary_id
        report["boundary_artifacts_checked"] = index_check(boundary_folder, boundary_folder / "artifact_hashes.json")
        boundary = read(boundary_folder / "report.json")
        if boundary["status"] != "passed" or any(b["result"]["failures"] for b in boundary["builds"]):
            raise ValueError("Boundary audit did not pass")
        if boundary["fixture_sha256"] != pc["contract_sha256"]:
            raise ValueError("Boundary fixture differs")
        if pc["status"] != "passed" or pc["algorithm_accuracy"] != "NOT_ACCEPTED" or not math["passed"]:
            raise ValueError("Expected port pass with distinct numerical acceptance")
        for relative, expected in math["source_hashes"].items():
            if relative.startswith("software/c_fixed/") and pc["source_hashes"].get(relative) != expected:
                raise ValueError("Math/PCM source snapshot mismatch: " + relative)
        report["supplemental_source_bindings"] = {}
        for relative, expected in pc["source_hashes"].items():
            if sha(PROJECT / relative) != expected:
                if relative != "scripts/run_c_fixed_full.py" or sha(PROJECT / relative) != boundary["build_helper_sha256"]:
                    raise ValueError("Current tested source differs: " + relative)
                report["supplemental_source_bindings"][relative] = {
                    "primary_snapshot_sha256": expected,
                    "supplemental_sha256": boundary["build_helper_sha256"],
                    "reason": "Future-run boundary orchestration added after immutable primary02; tested by supplemental audit"}
        if sha(PROJECT / "verification/c_fixed/full_published_boundaries.py") != boundary["auditor_sha256"]:
            raise ValueError("Boundary auditor changed")
        for name, expected in boundary["core_sha256"].items():
            if pc["source_hashes"]["software/c_fixed/" + name] != expected:
                raise ValueError("Boundary/PCM core differs")
        report["math_and_pcm_core_hashes_match"] = True
        report["current_sources_bound_to_primary_or_supplement"] = len(pc["source_hashes"])
        fixture = read(run / "contract/contract.json")
        package = run / "published_contract"
        report["snapshot_published_artifacts_checked"] = index_check(package, package / "artifact_hashes.json")
        publication = read(package / "PUBLISHED.json")
        for name, key in (("contract.json", "contract_sha256"),
                          ("artifact_hashes.json", "artifact_manifest_sha256"),
                          ("verification.json", "verification_sha256")):
            if sha(package / name) != publication[key]:
                raise ValueError("Publication binding mismatch")
        original = PROJECT.parent / "build/fixed_contract" / fixture["version"]
        report["original_published_artifacts_checked"] = index_check(original, original / "artifact_hashes.json")
        if sha(original / "contract.json") != fixture["published_contract_sha256"]:
            raise ValueError("Original publication identity changed")
        if sha(original / "artifact_hashes.json") != fixture["published_artifact_index_sha256"]:
            raise ValueError("Original artifact index changed")
        # Independent exact rational floor check on every observed C Mel cell.
        actual = np.fromfile(run / "pc/msvc_O2/bulk4096/frames.bin", dtype="<i8").reshape(-1, 2407)
        numerator, denominator = 4951760157141521, 4951760157141521099596496896
        floor_count = positive_floor = 0
        for row in actual:
            exponent = int(row[5])
            for m in range(26):
                mel = int(row[2316 + m])
                floored = mel * denominator <= numerator * (1 << -exponent)
                if floored != int(row[2368 + m]):
                    raise ValueError("Exact floor bit mismatch")
                if floored and int(row[2342 + m]) != -463571610:
                    raise ValueError("Floor log code mismatch")
                floor_count += floored
                positive_floor += floored and mel > 0
        report["exact_floor"] = {"cells": len(actual) * 26, "floored": floor_count,
                                 "positive_mel_floored": positive_floor, "mismatches": 0}
        ranges = {"window32": (11, 523, -(1 << 31), (1 << 31) - 1),
                  "input16_symmetric": (523, 1035, -32767, 32767),
                  "fft_re20": (1035, 1547, -(1 << 19), (1 << 19) - 1),
                  "fft_im20": (1547, 2059, -(1 << 19), (1 << 19) - 1),
                  "power40": (2059, 2316, 0, (1 << 40) - 1),
                  "mel60": (2316, 2342, 0, (1 << 60) - 1),
                  "log30": (2342, 2368, -(1 << 29), (1 << 29) - 1),
                  "mfcc40": (2394, 2407, -(1 << 39), (1 << 39) - 1)}
        report["observed_ranges"] = {}
        for name, (start, stop, lower, upper) in ranges.items():
            values = actual[:, start:stop]
            minimum, maximum = int(values.min()), int(values.max())
            if minimum < lower or maximum > upper:
                raise ValueError("Logical width violation: " + name)
            report["observed_ranges"][name] = {"min": minimum, "max": maximum, "values": values.size}
        arm = run / args.arm_id
        report["arm_artifacts_checked"] = index_check(arm, arm / "artifact_manifest.json")
        manifest = read(arm / "build_manifest.json")
        if manifest["status"] != "built_not_board_verified" or manifest["board_executed"] or manifest["timing_measured"]:
            raise ValueError("Unexpected ARM claim")
        for relative, entry in manifest["source_hashes"].items():
            if relative.startswith("core/"):
                if pc["source_hashes"]["software/c_fixed/" + Path(relative).name] != entry["sha256"]:
                    raise ValueError("ARM/PC source mismatch")
            elif relative.startswith("arm/") and sha(Path(entry["source"])) != entry["sha256"]:
                raise ValueError("Current ARM adapter changed")
        published = read(package / "contract.json")
        dev = next(case for case in published["cases"] if case["group"] == "development")
        expected = {
            "c_fixed_tables": (run / "contract/twiddle_re_im_s16le.bin").read_bytes() +
                              (run / "contract/mel_q16.bin").read_bytes(),
            "c_fixed_full_tables": (run / "contract/window_q30.bin").read_bytes() +
                                   (run / "contract/dct_q30.bin").read_bytes(),
            "c_fixed_full_dev_pcm": (package / dev["pcm"]["file"]).read_bytes(),
            "c_fixed_full_dev_mfcc": (package / dev["stages"]["mfcc_q24"]["file"]).read_bytes(),
        }
        def stage(name):
            entry = dev["stages"][name]
            return np.fromfile(package / entry["file"], dtype=entry["dtype"]).reshape(entry["shape"])
        s = stage("shift_s")
        meta = np.column_stack((np.arange(dev["frames"]), stage("frame_starts"), s, -27 - 2*s,
                               stage("mel_exponent"), stage("fft_overflow"),
                               stage("input_clips"), stage("bfp_clamped")))
        expected["c_fixed_full_dev_metadata"] = meta.astype("<i4").tobytes()
        report["elf_exact_bytes"] = {}
        for name, binary in manifest["binaries"].items():
            records = {}
            for symbol, value in expected.items():
                entry = binary["symbol_sizes"][symbol]
                actual_bytes = elf_bytes(Path(binary["path"]), int(entry["address"], 16), entry["bytes"])
                if actual_bytes != value:
                    raise ValueError("ELF contract bytes differ: " + name + "/" + symbol)
                records[symbol] = {"matched_bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            report["elf_exact_bytes"][name] = records
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    with args.report.with_suffix(".py").open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
