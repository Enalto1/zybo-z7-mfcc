"""Read-only comparison of pinned PS-only and fixed PS/PL PS7 configuration.

This checks configuration preservation, not implementation or physical DDR
operation. --run may be audited once its PS7 report exists. Write --out outside
both immutable runs; an existing output is never overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys


DEFAULT_BASELINE = Path("D:/2610_MFCC/build/arm_platform/reproduce_01_platform")
BASELINE_REPORT_SHA256 = "f2c701783582624dc07919f55177349cbce365fd72089396618f113a13332160"
REPORT_REL = "reports/ps7_parameters.tsv"
SYSTEM_TCL = "hardware/system/create_fixed_system.tcl"
# Every other property, including GP0 widths/security/thread configuration and
# the common IO PLL, must retain the baseline value exactly.
ALLOWED_CONFIG_CHANGES = {
    "CONFIG.PCW_USE_M_AXI_GP0": "enable PS master GP0",
    "CONFIG.PCW_M_AXI_GP0_FREQMHZ": "GP0 ACLK follows FCLK0",
    "CONFIG.PCW_EN_CLK0_PORT": "enable FCLK0 output",
    "CONFIG.PCW_EN_RST0_PORT": "enable FCLK_RESET0_N output",
    "CONFIG.PCW_FPGA_FCLK0_ENABLE": "derived FCLK0 enable",
    "CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ": "request 100 MHz FCLK0",
    "CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ": "derived 100 MHz FCLK0",
    "CONFIG.PCW_CLK0_FREQ": "derived FCLK0 frequency in Hz",
    "CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR0": "IO PLL to FCLK0 divisor",
    "CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR1": "IO PLL to FCLK0 divisor",
    "CONFIG.PCW_FCLK_CLK0_BUF": "FCLK0 clock buffering selection",
}
ALLOWED_METADATA_CHANGES = {
    "CONFIG.Component_Name": "BD-generated instance name",
    "CUSTOMIZATION_CRC": "derived PS7 customization checksum",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(values: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_parameters(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0] != "property\tvalue":
        raise ValueError(f"Invalid TSV header: {path}")
    result = {}
    for number, line in enumerate(lines[1:], 2):
        if not line:
            continue
        if "\t" not in line:
            raise ValueError(f"Missing TSV delimiter: {path}:{number}")
        key, value = line.split("\t", 1)
        if not key or key in result:
            raise ValueError(f"Empty or duplicate property: {path}:{number}: {key}")
        result[key] = value
    return result


def compare_parameters(baseline: dict[str, str], system: dict[str, str]) -> dict:
    """Pure comparison, also used by negative mutation checks."""
    failures = []
    missing = sorted(baseline.keys() - system.keys())
    added = sorted(system.keys() - baseline.keys())
    if missing or added:
        failures.append({"property_set_changed": {"missing": missing, "added": added}})
    allowed = ALLOWED_CONFIG_CHANGES | ALLOWED_METADATA_CHANGES
    changes = []
    for key in sorted(baseline.keys() & system.keys()):
        if baseline[key] != system[key]:
            change = dict(property=key, before=baseline[key], after=system[key],
                          allowed=key in allowed, reason=allowed.get(key, "must remain identical"))
            changes.append(change)
            if key not in allowed:
                failures.append(change)

    expected_numbers = {
        "CONFIG.PCW_USE_M_AXI_GP0": 1, "CONFIG.PCW_M_AXI_GP0_FREQMHZ": 100,
        "CONFIG.PCW_EN_CLK0_PORT": 1, "CONFIG.PCW_EN_RST0_PORT": 1,
        "CONFIG.PCW_FPGA_FCLK0_ENABLE": 1,
        "CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ": 100,
        "CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ": 100,
        "CONFIG.PCW_CLK0_FREQ": 100000000,
    }
    for key, expected in expected_numbers.items():
        try:
            correct = Decimal(system[key]) == Decimal(expected)
        except (KeyError, InvalidOperation):
            correct = False
        if not correct:
            failures.append(dict(property=key, expected=expected, actual=system.get(key)))
    try:
        divisors = [Decimal(system[f"CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR{i}"]) for i in (0, 1)]
        valid_divisors = all(d == d.to_integral_value() and 1 <= d <= 63 for d in divisors)
        valid_divisors &= Decimal(system["CONFIG.PCW_IO_IO_PLL_FREQMHZ"]) == divisors[0] * divisors[1] * 100
    except (KeyError, InvalidOperation):
        valid_divisors = False
    if not valid_divisors:
        failures.append({"FCLK0_divisors": "must be integers 1..63 and divide unchanged IO PLL to 100 MHz"})
    for key, expected in {
        "CONFIG.PCW_FCLK0_PERIPHERAL_CLKSRC": "IO PLL",
        "VLNV": "xilinx.com:ip:processing_system7:5.5",
        "CONFIG.Component_Name": "zybo_z7_20_fixed_processing_system7_0_0",
    }.items():
        if system.get(key) != expected:
            failures.append(dict(property=key, expected=expected, actual=system.get(key)))
    if system.get("CONFIG.PCW_FCLK_CLK0_BUF") not in {"FALSE", "TRUE"}:
        failures.append({"FCLK0_buffer": "expected a Boolean Vivado value"})
    if not re.fullmatch(r"[0-9a-fA-F]{8}", system.get("CUSTOMIZATION_CRC", "")):
        failures.append({"CUSTOMIZATION_CRC": "expected eight hexadecimal digits"})

    # These subsets are reporting aids; the strict comparison above covers ALL
    # properties, even those not matched by these category descriptions.
    groups = {
        "DDR": lambda key: "DDR" in key or "DCI" in key,
        "MIO_and_banks": lambda key: "MIO" in key or "BANK" in key,
        "CPU_APU_and_shared_PLL": lambda key: any(s in key for s in ("CPU", "APU", "PLL", "CRYSTAL")),
        "peripheral_and_other_configuration": lambda key: key.startswith("CONFIG.") and key not in allowed,
    }
    preserved = {}
    for name, select in groups.items():
        before = {k: v for k, v in baseline.items() if select(k) and k not in allowed}
        after = {k: system[k] for k in before if k in system}
        preserved[name] = dict(properties=len(before), identical=before == after,
                               baseline_sha256=canonical_sha(before), system_sha256=canonical_sha(after))
    return dict(status="FAIL" if failures else "PASS", property_count=len(system),
                baseline_property_count=len(baseline), differences=changes,
                failures=failures, preserved_groups=preserved)


def audit(baseline: Path, run: Path) -> dict:
    baseline_report, system_report = baseline / REPORT_REL, run / REPORT_REL
    if sha(baseline_report) != BASELINE_REPORT_SHA256:
        raise ValueError("PS-only baseline report no longer matches the pinned reproduce_01 snapshot")
    result = compare_parameters(read_parameters(baseline_report), read_parameters(system_report))
    snapshots = {}
    for label, path in {"baseline_parameters": baseline_report, "system_parameters": system_report,
                        "baseline_manifest": baseline / "platform_manifest.json",
                        "system_manifest": run / "run_manifest.json"}.items():
        snapshots[label] = dict(path=str(path), sha256=sha(path))
    bm = read_json(baseline / "platform_manifest.json")
    sm = read_json(run / "run_manifest.json")
    spec = sm["board_source"]
    for key in ("repository", "commit", "board_part", "target_part"):
        if spec[key] != bm[key]:
            raise ValueError(f"Board provenance mismatch: {key}")
    original_board = {Path(entry["path"]).name: entry for entry in bm["official_sources"]}
    board_hashes = {}
    for entry in spec["files"]:
        path = (run / "board_source" / entry["local"]).resolve()
        if not path.is_relative_to(run / "board_source"):
            raise ValueError("Board source path escapes the run snapshot")
        original = original_board[path.name]
        if sha(path) != entry["sha256"] or entry["sha256"] != original["sha256"]:
            raise ValueError(f"Board source bytes changed: {path.name}")
        if sha(Path(original["path"])) != original["sha256"]:
            raise ValueError(f"PS-only original board source changed: {path.name}")
        board_hashes[entry["local"]] = entry["sha256"]
    if {Path(k).name for k in board_hashes} != {"board.xml", "part0_pins.xml", "preset.xml", "License.txt"}:
        raise ValueError("Missing or unexpected pinned board files")
    snapshot = run / "source" / SYSTEM_TCL
    if sha(snapshot) != sm["source_sha256"][SYSTEM_TCL]:
        raise ValueError("System Tcl snapshot hash differs from its manifest")
    snapshots["system_tcl"] = dict(path=str(snapshot), sha256=sha(snapshot))
    for source, expected in bm["source_hashes"].items():
        snapshot = baseline / "provenance" / Path(source).name
        if sha(snapshot) != expected:
            raise ValueError(f"PS-only source snapshot changed: {snapshot.name}")
        snapshots["baseline_" + snapshot.name] = dict(path=str(snapshot), sha256=expected)
    # A report may be checked before the implementation run is finalized. Do
    # not invent a completion claim or require a manifest that is not yet made.
    artifact_manifest = run / "artifact_hashes.json"
    artifact_authenticated = False
    if artifact_manifest.exists():
        hashes = read_json(artifact_manifest)
        if REPORT_REL not in hashes or hashes[REPORT_REL] != sha(system_report):
            raise ValueError("System PS7 report differs from the finalized artifact manifest")
        snapshots["system_artifact_manifest"] = dict(path=str(artifact_manifest), sha256=sha(artifact_manifest))
        artifact_authenticated = True
    result.update(snapshots=snapshots, board_source_sha256=board_hashes,
                  board_commit=spec["commit"], system_run_status_at_audit=sm["status"],
                  finalized_report_authenticated=artifact_authenticated,
                  allowed_configuration_changes=ALLOWED_CONFIG_CHANGES,
                  allowed_metadata_changes=ALLOWED_METADATA_CHANGES)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    baseline, run, out = args.baseline.resolve(), args.run.resolve(), args.out.resolve()
    if out.exists() or out.is_relative_to(baseline) or out.is_relative_to(run):
        parser.error("--out must be a new path outside both immutable runs")
    result = dict(status="FAIL", audited_at_utc=datetime.now(timezone.utc).isoformat(),
                  auditor_sha256=sha(Path(__file__)), baseline=str(baseline), run=str(run),
                  scope="PS7 configuration preservation only", physical_board_accessed=False,
                  physical_DDR_operation_verified=False)
    try:
        result["evidence"] = audit(baseline, run)
        result["status"] = result["evidence"]["status"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["error"] = str(error)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}, indent=2))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    sys.exit(main())
