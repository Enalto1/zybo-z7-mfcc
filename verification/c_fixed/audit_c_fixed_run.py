"""Read-only final integrity and actual ARM ELF coefficient audit.

Writes a fresh report only. No board access and no evaluation inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_index(root, index):
    count = 0
    for relative, expected in read(index).items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Hash index escapes run")
        if sha(path) != expected:
            raise ValueError("Artifact hash mismatch: " + str(path))
        count += 1
    return count


def elf_bytes(path, address, length):
    data = path.read_bytes()
    if data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("Expected little-endian ELF32")
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if header[1] != 40:
        raise ValueError("Expected ARM machine")
    phoff, entry_size, count = header[4], header[8], header[9]
    for i in range(count):
        kind, offset, vaddr, _, filesz, _, _, _ = struct.unpack_from(
            "<IIIIIIII", data, phoff + i * entry_size)
        if kind == 1 and vaddr <= address and address + length <= vaddr + filesz:
            start = offset + address - vaddr
            return data[start:start + length]
    raise ValueError("Symbol is not fully contained in an ELF load segment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--arm-id", default="arm_01")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--legacy-before", type=Path)
    args = parser.parse_args()
    out = args.run.resolve()
    report = {"status": "failed", "run": str(out), "board_executed": False,
              "auditor_sha256": sha(Path(__file__))}
    try:
        report["pc_artifacts"] = check_index(out, out / "pc_artifact_hashes.json")
        pc = read(out / "pc_results.json")
        if pc["status"] != "passed" or pc["algorithm_accuracy_pass"] is not None:
            raise ValueError("Expected bit-port pass with separate unpublished numerical acceptance")
        if sha(out / "contract/contract.json") != pc["contract_sha256"]:
            raise ValueError("PC contract identity mismatch")
        contract = read(out / "contract/contract.json")
        # Check that float64 diagnostics made the same floor decision as the
        # exact published rational, including positive Mel integers below it.
        numerator = 4951760157141521
        denominator = 4951760157141521099596496896
        floor_cells = positive_floor_cells = floor_disagreements = 0
        actual = out / "pc/msvc_O2/actual_i64le.bin"
        with actual.open("rb") as handle:
            for frame in range(contract["original_frames"]):
                offset = frame * contract["values_per_record"] * 8
                handle.seek(offset)
                exponent = struct.unpack("<6q", handle.read(48))[2]
                handle.seek(offset + 1287 * 8)
                for value in struct.unpack("<26Q", handle.read(26 * 8)):
                    left = value * denominator
                    right = numerator
                    if exponent < 0:
                        right *= 1 << -exponent
                    else:
                        left *= 1 << exponent
                    below = left < right
                    floor_cells += below
                    positive_floor_cells += below and value != 0
                    floor_disagreements += below != (math.ldexp(float(value), exponent) < 1e-12)
        report["exact_floor_diagnostic"] = {
            "frames": contract["original_frames"], "cells": contract["original_frames"] * 26,
            "exact_below_floor": floor_cells, "positive_integer_below_floor": positive_floor_cells,
            "float64_predicate_disagreements": floor_disagreements,
            "scope": "host diagnostic only, no C log/floor implementation"}
        if floor_disagreements:
            raise ValueError("Float64 floor diagnostics differ from exact rational")
        arm = out / args.arm_id
        report["arm_artifacts"] = check_index(arm, arm / "artifact_manifest.json")
        manifest = read(arm / "build_manifest.json")
        if manifest["status"] != "built_not_board_verified" or manifest["board_executed"]:
            raise ValueError("Unexpected ARM completion claim")
        expected_tables = (out / "contract/twiddle_re_im_s16le.bin").read_bytes()
        expected_tables += (out / "contract/mel_u32le.bin").read_bytes()
        if len(expected_tables) != 30824:
            raise ValueError("Unexpected table size")
        report["elf_tables"] = {}
        for name, binary in manifest["binaries"].items():
            symbol = binary["symbol_sizes"]["c_fixed_tables"]
            content = elf_bytes(Path(binary["path"]), int(symbol["address"], 16), symbol["bytes"])
            if content != expected_tables:
                raise ValueError("ELF coefficient bytes differ: " + name)
            report["elf_tables"][name] = {"matched_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()}
        if args.legacy_before:
            project = Path(__file__).resolve().parents[2]
            previous = read(args.legacy_before)
            for relative, expected in previous.items():
                if sha(project / relative) != expected:
                    raise ValueError("Legacy file changed: " + relative)
            report["legacy_C_ARM_files_unchanged"] = len(previous)
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
