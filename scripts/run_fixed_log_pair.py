#!/usr/bin/env python3
"""Snapshot and test an ordered two-lane log candidate against the frozen oracle.

The candidate directory supplies mfcc_fixed_log_pair.sv. Existing scalar log and
floor RTL are copied unchanged. --prepare-only performs no Vivado invocation.
Every run uses a new build/fixed_log_pair/<run-id> and retains failures/exit codes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PINNED_CONTRACT_SHA256 = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"
DEFAULT_CONTRACT = ROOT.parent / "build/fixed_contract/v2_pcm16_mfcc40_20261004_r2"
TB_REL = "verification/fixed/log_dct/tb_mfcc_fixed_log_pair.sv"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_bytes((json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    require(sha(dst) == sha(src), "snapshot differs: " + str(src))
    return sha(dst)


def snapshot_oracle(contract, out):
    publication = json.loads((contract / "PUBLISHED.json").read_bytes())
    require(publication["status"] == "PUBLISHED", "contract is not published")
    require(sha(contract / "contract.json") == publication["contract_sha256"] == PINNED_CONTRACT_SHA256,
            "published v2 contract pin mismatch")
    require(sha(contract / "artifact_hashes.json") == publication["artifact_manifest_sha256"],
            "published inventory pin mismatch")
    require(sha(contract / "verification.json") == publication["verification_sha256"],
            "published verification pin mismatch")
    require(json.loads((contract / "verification.json").read_bytes())["status"] == "PASS",
            "published verification status")
    inventory = json.loads((contract / "artifact_hashes.json").read_bytes())
    model_hashes, contract_hashes = {}, {}
    for rel, expected in inventory.items():
        if rel.startswith("model_snapshot/software/fixed_model/"):
            src = (contract / rel).resolve()
            require(src.is_relative_to(contract.resolve()), "oracle path escapes contract")
            require(sha(src) == expected, "oracle source hash mismatch: " + rel)
            model_hashes[rel] = snapshot(src, out / "oracle" / rel)
    for rel in ("PUBLISHED.json", "contract.json", "artifact_hashes.json", "verification.json", "vectors/boundaries.json"):
        src = contract / rel
        if rel in inventory:
            require(sha(src) == inventory[rel], "contract member hash mismatch: " + rel)
        contract_hashes[rel] = snapshot(src, out / "oracle" / rel)
    # Import only the copied, hash-verified immutable oracle. No cache writes.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(out / "oracle/model_snapshot"))
    from software.fixed_model.full_integer import log_integer
    return log_integer, model_hashes, contract_hashes


def prepare_vectors(log_integer, out):
    words = [((1 << 60) - 1, 0, 0), (0, 0, 0), ((1 << 31) + 1, 0, 1),
             (0, -3, 0), (0, 25, 0), (0, 0, 1), ((1 << 59) + 123, -2, 0)]
    boundaries = json.loads((out / "oracle/vectors/boundaries.json").read_bytes())["log"]
    for case in boundaries:
        value, exponent = log_integer(case["T"], case["exponent"])
        require(value == case["expected"] and exponent == case["floor"], "published log boundary mismatch")
        words.append((case["T"], case["s"], 0))
    rng = random.Random(20261005)
    for index in range(512):
        words.append((rng.randrange(1, 1 << 60) if index % 2 == 0 else 0,
                      rng.randrange(-2, 25), int(index % 31 == 0)))
    if len(words) % 2 == 0:
        words.append((1 << 40, 0, 0))
    inputs, expected = [str(len(words))], []
    for ordinal, (value, exponent, error) in enumerate(words):
        frame, band = divmod(ordinal, 26)
        last = int(band == 25)
        if not -2 <= exponent <= 24:
            result, floor, result_error = 0, False, 1
        else:
            result, floor = log_integer(value, -43 - 2 * exponent)
            result_error = error
        inputs.append(f"{value} {frame} {band} {exponent} {last} {error}")
        expected.append(f"{result} {frame} {band} {exponent} {last} {int(floor)} {result_error}")
    for name, rows in (("log_pair_input.txt", inputs), ("log_pair_expected.txt", expected)):
        (out / name).write_bytes(("\n".join(rows) + "\n").encode("ascii"))
    return len(words)


def check_pair_style(path):
    source = path.read_text(encoding="utf-8")
    cleaned = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    forbidden = re.findall(r"\b(?:for|function|task|initial|while|repeat|forever|real|shortreal|casex)\b|\.\*", cleaned)
    require(not forbidden, "pair contains forbidden RTL syntax: " + str(forbidden))
    ff, comb = len(re.findall(r"\balways_ff\b", cleaned)), len(re.findall(r"\balways_comb\b", cleaned))
    require((ff, comb) == (1, 1), "pair requires one always_ff and one always_comb")
    require(not re.search(r"@\s*\([^)]*negedge", cleaned), "pair has asynchronous/negative-edge logic")
    return dict(status="PASS", always_ff=ff, always_comb=comb, scope="static source checks only")


def prepare_tcl(out):
    tb = (out / "source" / TB_REL).read_text(encoding="utf-8")
    for name in ("log_pair_input.txt", "log_pair_expected.txt", "protocol.json"):
        tb = tb.replace('"' + name + '"', '"' + (out / name).as_posix() + '"')
    (out / "tb_mfcc_fixed_log_pair_run.sv").write_bytes(tb.encode("utf-8"))
    sources = [out / "source/hardware/fixed/log_dct/mfcc_fixed_floor_threshold.sv",
               out / "source/hardware/fixed/log_dct/mfcc_fixed_log.sv",
               out / "source/candidate/mfcc_fixed_log_pair.sv", out / "tb_mfcc_fixed_log_pair_run.sv"]
    tcl = [f"cd {{{out.as_posix()}}}",
           f"create_project log_pair_unit {{{(out / 'project').as_posix()}}} -part xc7z020clg400-1",
           "set_property target_language Verilog [current_project]",
           "add_files -fileset sim_1 {" + " ".join("{" + p.as_posix() + "}" for p in sources) + "}",
           "set_property top tb_mfcc_fixed_log_pair [get_filesets sim_1]",
           "set_property xsim.simulate.runtime all [get_filesets sim_1]",
           "launch_simulation", "close_sim", "close_project",
           f"if {{![file exists {{{(out / 'protocol.json').as_posix()}}}]}} {{error {{Missing log pair PASS protocol}}}}"]
    # This arithmetic-only project never compiles PS7 or any installed library.
    (out / "run_log_pair.tcl").write_bytes(("\n".join(tcl) + "\n").encode("utf-8"))


def validate_protocol(out, expected_count):
    path = out / "protocol.json"
    require(path.is_file(), "TB did not produce a PASS protocol file")
    protocol = json.loads(path.read_bytes())
    require(protocol.get("status") == "PASS", "TB protocol status is not PASS")
    require(protocol.get("transactions") == protocol.get("accepted") == expected_count, "TB transaction count mismatch")
    for key in ("cycles", "adjacent", "reorder_wait", "stalls"):
        require(isinstance(protocol.get(key), int) and protocol[key] > 0, "TB coverage missing: " + key)
    for key in ("reset_pending", "reset_held", "odd_drain"):
        require(protocol.get(key) is True, "TB coverage missing: " + key)
    console = (out / "console.log").read_text(encoding="utf-8", errors="replace")
    require(re.search(r"PASS LOG_PAIR transactions=" + str(expected_count) + r"\b", console) is not None,
            "Vivado console is missing TB PASS marker")
    require(not re.search(r"\bFatal:|\$fatal|ERROR: \[Simulator", console), "simulation fatal/error in console")
    return protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate", required=True, type=Path, help="directory containing mfcc_fixed_log_pair.sv")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--prepare-only", action="store_true", help="snapshot and generate vectors without starting Vivado")
    parser.add_argument("--vivado", type=Path, default=Path("C:/Xilinx/Vivado/2024.2/bin/vivado.bat"))
    args = parser.parse_args()
    require(args.run_id.replace("_", "").replace("-", "").isalnum(), "simple fresh run-id required")
    out = (ROOT.parent / "build/fixed_log_pair" / args.run_id).resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(status="preparing", started_at_utc=utc_now(), run_id=args.run_id,
                    candidate=str(args.candidate.resolve()), contract=str(args.contract.resolve()),
                    tool="Vivado 2024.2", part="xc7z020clg400-1", source_sha256={},
                    vivado_started=False, vivado_exit_code=None, protocol=None,
                    simulation="NOT_RUN", synthesis="NOT_RUN", board="NOT_RUN",
                    numerical_accuracy="NOT_ACCEPTED", evaluation_audio_used=False)
    dump(out / "run_manifest.json", manifest)
    try:
        source_paths = [Path(__file__), ROOT / TB_REL,
                        ROOT / "hardware/fixed/log_dct/mfcc_fixed_log.sv",
                        ROOT / "hardware/fixed/log_dct/mfcc_fixed_floor_threshold.sv"]
        for source in source_paths:
            rel = source.relative_to(ROOT).as_posix()
            manifest["source_sha256"][rel] = snapshot(source, out / "source" / rel)
        for name in ("mfcc_fixed_log_pair.sv", "mfcc_fixed_log_dct.sv", "DESIGN.md"):
            source = args.candidate / name
            if name == "mfcc_fixed_log_pair.sv" or source.exists():
                rel = "candidate/" + name
                manifest["source_sha256"][rel] = snapshot(source, out / "source" / rel)
        manifest["static"] = check_pair_style(out / "source/candidate/mfcc_fixed_log_pair.sv")
        log_integer, model_hashes, contract_hashes = snapshot_oracle(args.contract, out)
        manifest.update(oracle_sha256=model_hashes, contract_sha256=contract_hashes)
        transactions = prepare_vectors(log_integer, out)
        manifest["transactions"] = transactions
        manifest["vector_sha256"] = {name: sha(out / name) for name in ("log_pair_input.txt", "log_pair_expected.txt")}
        prepare_tcl(out)
        manifest["generated_sha256"] = {name: sha(out / name) for name in ("run_log_pair.tcl", "tb_mfcc_fixed_log_pair_run.sv")}
        command = [str(args.vivado), "-mode", "batch", "-notrace", "-source", str(out / "run_log_pair.tcl")]
        manifest["command"] = command
        if args.prepare_only:
            manifest["status"] = "prepared"
        else:
            manifest.update(status="running", vivado_started=True)
            dump(out / "run_manifest.json", manifest)
            with (out / "console.log").open("w", encoding="utf-8") as console:
                process = subprocess.run(command, cwd=out, stdout=console, stderr=subprocess.STDOUT)
            manifest["vivado_exit_code"] = process.returncode
            # Preserve available protocol even when Vivado returns a failure.
            if (out / "protocol.json").exists():
                manifest["protocol"] = json.loads((out / "protocol.json").read_bytes())
            require(process.returncode == 0, "Vivado failed with exit code " + str(process.returncode))
            manifest["protocol"] = validate_protocol(out, transactions)
            manifest.update(status="complete", simulation="PASS")
    except BaseException as error:
        manifest.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        manifest["finished_at_utc"] = utc_now()
        manifest["snapshot_sha256_after"] = {rel: sha(out / "source" / rel) for rel in manifest["source_sha256"]}
        if manifest["snapshot_sha256_after"] != manifest["source_sha256"]:
            manifest.update(status="failed", error="run snapshot source changed during execution")
        dump(out / "run_manifest.json", manifest)
        dump(out / "artifact_hashes.json", {p.relative_to(out).as_posix(): sha(p) for p in out.rglob("*")
                                           if p.is_file() and p.name != "artifact_hashes.json"})
        print(json.dumps(dict(run=str(out), status=manifest["status"], vivado_exit_code=manifest["vivado_exit_code"],
                              simulation=manifest["simulation"], protocol=manifest["protocol"]), indent=2))
    require(manifest["status"] in ("complete", "prepared"), "run did not complete")


if __name__ == "__main__":
    main()
