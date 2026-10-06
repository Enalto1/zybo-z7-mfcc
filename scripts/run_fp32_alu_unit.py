"""Run the real Vivado FP IP ALU and preserve protocol/numerical evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT.parent
VIVADO = Path("C:/Xilinx/Vivado/2024.2/bin/vivado.bat")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def float_value(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ip-run", type=Path, default=WORK / "build/fp32_hw/ip_04")
    args = parser.parse_args()
    if not args.run_id.startswith("alu_") or not args.run_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Use a fresh simple run-id beginning alu_")
    out = WORK / "build/fp32_hw" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "preparing", "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "vendor_arithmetic": "Generated Vivado 2024.2 FP multiply/addsub/log models",
                "physical_board_accessed": False, "evaluation_audio_read": False,
                "asymmetric_channel_stimulus": "Actual vendor channel ready signals observed without forcing; sent flags checked for duplicate sends. Different-cycle coverage is reported, not assumed.",
                "source_hashes": {}, "ip_run": str(args.ip_run.resolve())}
    sources = [PROJECT / "hardware/fp32/rtl/fp32_alu.sv",
               PROJECT / "verification/fp32/f1_alu/tb_fp32_alu_unit.sv",
               PROJECT / "scripts/fp32_alu_unit.tcl", Path(__file__)]
    try:
        for source in sources:
            relative = source.relative_to(PROJECT)
            destination = out / "source" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            manifest["source_hashes"][relative.as_posix()] = sha(destination)
        ip_manifest_path = args.ip_run / "ip_manifest.json"
        ip_manifest = json.loads(ip_manifest_path.read_text(encoding="utf-8-sig"))
        manifest["ip_manifest_sha256"] = sha(ip_manifest_path)
        for relative, expected in ip_manifest["output_hashes"].items():
            if sha(args.ip_run / relative) != expected:
                raise ValueError(f"IP artifact changed: {relative}")
        command = [str(VIVADO), "-mode", "batch", "-source", str(out / "source/scripts/fp32_alu_unit.tcl"),
                   "-tclargs", str(out), str(args.ip_run.resolve())]
        manifest["command"] = command
        dump(out / "run_manifest.json", manifest)
        with (out / "console.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
        manifest["vivado_exit_code"] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError("Vivado failed; inspect console.log")
        protocol = (out / "alu_protocol.txt").read_text().strip()
        if not protocol.startswith("PASS requests=11 responses=10 aborted=1 completed=10 "):
            raise RuntimeError("ALU protocol test did not complete")
        manifest["protocol"] = protocol
        protocol_fields = dict(word.split("=", 1) for word in protocol.split()[1:])
        manifest["different_cycle_a_b_acceptance_observed"] = int(protocol_fields["asymmetric_accept_cycles"]) > 0
        manifest["disabled_clock_stability_checks"] = {
            name: int(protocol_fields[name + "_paused_cycles"]) for name in ("mul", "add", "log")}
        if min(manifest["disabled_clock_stability_checks"].values()) < 100:
            raise RuntimeError("Insufficient actual-IP clock-enable pause coverage")
        measurements = []
        for expected_id, line in enumerate((out / "alu_results.txt").read_text().splitlines()):
            words = line.split()
            if len(words) != 8 or int(words[0]) != expected_id:
                raise ValueError("Unexpected ALU result order/layout")
            operation = int(words[1])
            a_bits, b_bits, actual_bits = [int(word, 16) for word in words[2:5]]
            a, b, actual = [float_value(bits) for bits in (a_bits, b_bits, actual_bits)]
            exact = a * b if operation == 0 else a + b if operation == 1 else a - b if operation == 2 else math.log(a)
            expected_bits = float_bits(exact)
            error = abs(actual - exact)
            passed = (math.isfinite(actual) and (error <= 1e-3 + 1e-5 * abs(exact)
                                                if operation == 3 else actual_bits == expected_bits))
            measurements.append({"id": expected_id, "operation": operation,
                                 "a_bits": f"{a_bits:08x}", "b_bits": f"{b_bits:08x}",
                                 "actual_bits": f"{actual_bits:08x}", "reference_rounded_bits": f"{expected_bits:08x}",
                                 "absolute_error": error, "passed": passed,
                                 "request_cycle": int(words[5]), "result_seen_cycle": int(words[6]),
                                 "response_accepted_cycle": int(words[7])})
        if len(measurements) != 10 or not all(record["passed"] for record in measurements):
            manifest["measurements"] = measurements
            raise RuntimeError("ALU numerical checks failed")
        manifest["measurements"] = measurements
        for source in sources:
            if sha(source) != manifest["source_hashes"][source.relative_to(PROJECT).as_posix()]:
                raise RuntimeError("ALU source changed during run")
        manifest["status"] = "passed"
        return 0
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        raise
    finally:
        manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        dump(out / "run_manifest.json", manifest)
        artifacts = {path.relative_to(out).as_posix(): sha(path)
                     for path in sorted(out.rglob("*")) if path.is_file() and path.name != "artifact_manifest.json"}
        dump(out / "artifact_manifest.json", artifacts)
        print(json.dumps({"run": str(out), "status": manifest["status"]}))


if __name__ == "__main__":
    raise SystemExit(main())
