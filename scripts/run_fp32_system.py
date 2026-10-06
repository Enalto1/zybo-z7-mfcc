"""Freeze IP04 FP32 arithmetic, test its AXI transport, and build a PS/PL system.

No hardware access. Coordinate the Vivado/XSim slot before a non-prepare mode.
Use --resume only to advance an intact prepared/simulated run; failed runs are
retained and require a new run ID. All bitstreams require the real-IP AXI gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT.parent / "build"
VIVADO = Path("C:/Xilinx/Vivado/2024.2/bin/vivado.bat")
GOLD = BUILD / "fp32_hw/continuous_development_01"
SYNTH = BUILD / "fp32_hw/synth_compact_01"
IP = BUILD / "fp32_hw/ip_04"
IP_NAMES = ("fp32_mul", "fp32_addsub", "fp32_log", "fp32_pcm16", "fp32_fft512")
GOLD_INDEX_SHA = "ecc510646d9c75c496f4850b09a9943fa9df78f099d67b7ff5e639b8c647d7ac"
SYNTH_INDEX_SHA = "81b16d1b84e6df5323efd071972b8f03333528131a168c239c7179c72f231d08"
IP_MANIFEST_SHA = "d3b13e3f597f53322ec13f789c117cff42d656ae707b10d69c0ede2213f545f3"
PCM_SHA = "026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32"
TOLERANCE_SHA = "64ad3ab3a0ae050562b9ccfab7b3c8e7fcc90eb527eb6811cfcbbd4c07a9d2b6"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def process_guard():
    command = "Get-Process -Name vivado,xsim,xsimk,xelab,xvlog,xvhdl -ErrorAction SilentlyContinue | Select-Object Id,ProcessName | ConvertTo-Json -Compress; exit 0"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Cannot inspect competing Vivado jobs: " + result.stderr)
    if result.stdout.strip() not in ("", "null"):
        raise RuntimeError("Another Vivado/XSim job is active: " + result.stdout.strip())
    return dict(checked_at_utc=now(), active_jobs=[])


def authenticated_index(root, expected):
    path = root / "artifact_manifest.json"
    if sha(path) != expected:
        raise ValueError("Baseline index identity changed: " + str(path))
    return read(path)


def verify_indexed(root, index, relative):
    path = root / relative
    if index.get(relative) != sha(path):
        raise ValueError("Baseline indexed artifact changed: " + str(path))
    return path


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def snapshot(out):
    gi = authenticated_index(GOLD, GOLD_INDEX_SHA)
    si = authenticated_index(SYNTH, SYNTH_INDEX_SHA)
    gold = read(verify_indexed(GOLD, gi, "run_manifest.json"))
    synth = read(verify_indexed(SYNTH, si, "run_manifest.json"))
    if gold["status"] != "passed" or not gold["protocol_passed"] or not gold["numeric_acceptance_passed"]:
        raise ValueError("Continuous development baseline is incomplete")
    if synth["vivado_exit_code"] != 0 or synth["source_changed_after_snapshot"]:
        raise ValueError("Compact synthesis baseline is incomplete")
    if sha(IP / "ip_manifest.json") != IP_MANIFEST_SHA:
        raise ValueError("IP04 manifest changed")
    if gold["ip_manifest_sha256"] != IP_MANIFEST_SHA or synth["ip_manifest_sha256"] != IP_MANIFEST_SHA:
        raise ValueError("FP32 baselines used a different IP revision")
    ipmeta = read(IP / "ip_manifest.json")
    for relative, expected in ipmeta["output_hashes"].items():
        if sha(IP / relative) != expected:
            raise ValueError("IP04 product changed: " + relative)
    paths = sorted((ROOT / "hardware/fp32/rtl").glob("*.sv"))
    if len(paths) != 6:
        raise ValueError("Expected exactly six verified FP32 RTL sources")
    for source in paths:
        relative = source.relative_to(ROOT).as_posix()
        if sha(source) != gold["source_hashes"][relative] or sha(source) != synth["source_hashes"][relative]:
            raise ValueError("Verified FP32 arithmetic RTL changed: " + relative)
        verify_indexed(GOLD, gi, "source/" + relative)
        verify_indexed(SYNTH, si, "source/" + relative)
    transport = [ROOT / "hardware/system_fp32" / name for name in
                 ("fp32_core_adapter.sv", "fp32_mmio.sv", "fp32_accel_top.sv")]
    paths += transport + sorted((ROOT / "hardware/system_fp32").glob("*.tcl"))
    paths += sorted((ROOT / "verification/system_fp32").glob("*.sv"))
    paths += sorted((ROOT / "verification/system_fp32").glob("*.py"))
    paths += [Path(__file__), ROOT / "hardware/system_fp32/REGISTER_CONTRACT.md",
              ROOT / "hardware/platform/board_source.json", ROOT / "AGENTS.md",
              ROOT / "docs/RTL_CODING_RULES.md", ROOT / "docs/MFCC_SPEC.md",
              ROOT / "hardware/fp32/ip/IP_CONTRACT.md", ROOT / "verification/c/tolerances.json",
              ROOT / "verification/system/audit_ps_configuration.py"]
    hashes = {}
    for source in sorted(set(paths)):
        relative = source.relative_to(ROOT).as_posix()
        copy(source, out / "source" / relative)
        hashes[relative] = sha(source)
    if hashes["verification/c/tolerances.json"] != TOLERANCE_SHA:
        raise ValueError("Frozen tolerances changed")
    for name, expected in synth["coefficient_file_hashes"].items():
        source = verify_indexed(GOLD, gi, "coefficients/" + name)
        verify_indexed(SYNTH, si, "coefficients/" + name)
        if sha(source) != expected or sha(SYNTH / "coefficients" / name) != expected:
            raise ValueError("Verified FP32 coefficient ROM changed: " + name)
        copy(source, out / "coefficients" / name)
    for name in IP_NAMES:
        source = IP / "project/fp32_ips.srcs/sources_1/ip" / name / (name + ".xci")
        copy(source, out / "ip" / name / source.name)
    copy(IP / "ip_manifest.json", out / "provenance/ip_manifest.json")
    copy(GOLD / "run_manifest.json", out / "provenance/continuous_manifest.json")
    copy(SYNTH / "run_manifest.json", out / "provenance/synth_manifest.json")
    spec = read(ROOT / "hardware/platform/board_source.json")
    cache = BUILD / "arm_platform/officialsource" / ("digilent-vivado-boards-" + spec["commit"])
    for entry in spec["files"]:
        source = cache / entry["local"]
        if sha(source) != entry["sha256"]:
            raise ValueError("Pinned official board source changed: " + str(source))
        copy(source, out / "board_source" / entry["local"])
    return hashes, spec


def stage_inputs(out):
    gi = authenticated_index(GOLD, GOLD_INDEX_SHA)
    pcm_path = BUILD / "python_reference/reproduce_01/development/8463-294828-0037/input_s16le.pcm"
    if sha(pcm_path) != PCM_SHA or pcm_path.stat().st_size != 85920 * 2:
        raise ValueError("Unmodified development PCM identity/count changed")
    relative = "arrays/8463-294828-0037/"
    meta = read(verify_indexed(GOLD, gi, relative + "arrays.json"))["arrays"]["mfcc"]
    mfcc_path = verify_indexed(GOLD, gi, relative + "mfcc.bin")
    if meta["shape"] != [534, 13] or meta["dtype"] != "<f4" or not meta["finite"] or sha(mfcc_path) != meta["sha256"] or mfcc_path.stat().st_size != 534 * 13 * 4:
        raise ValueError("Preserved MFCC array metadata changed")
    pcm_bytes = pcm_path.read_bytes()[:672 * 2]
    mfcc_bytes = mfcc_path.read_bytes()[:2 * 13 * 4]
    (out / "pcm.mem").write_text("".join(f"{word:04x}\n" for (word,) in struct.iter_unpack("<H", pcm_bytes)), encoding="ascii")
    (out / "mfcc.mem").write_text("".join(f"{word:08x}\n" for (word,) in struct.iter_unpack("<I", mfcc_bytes)), encoding="ascii")
    for filename, payload, fmt in (("pcm.mem", pcm_bytes, "<H"), ("mfcc.mem", mfcc_bytes, "<I")):
        rebuilt = b"".join(struct.pack(fmt, int(line, 16)) for line in (out / filename).read_text().splitlines())
        if rebuilt != payload:
            raise ValueError("Transport vector conversion changed bits: " + filename)
    return dict(id="8463-294828-0037", samples_per_clip=672, frames_per_clip=2,
                repeated_clips=2, compared_records=52, first_sample=0,
                original_pcm_sha256=PCM_SHA, selected_pcm_sha256=hashlib.sha256(pcm_bytes).hexdigest(),
                reference_mfcc_sha256=sha(mfcc_path), selected_mfcc_sha256=hashlib.sha256(mfcc_bytes).hexdigest(),
                reference_run=str(GOLD), exact_bits_required=True, metadata_mismatches_allowed=0)


def xci_signature(path):
    ip = read(path)["ip_inst"]
    parameters = {}
    for group in ("component_parameters", "model_parameters"):
        parameters[group] = {name: [entry["value"] for entry in entries]
                             for name, entries in ip["parameters"][group].items()}
    return dict(component_reference=ip["component_reference"], ip_revision=ip["ip_revision"], parameters=parameters)


def audit_imported_ip(out, directory, label):
    evidence = []
    for name in IP_NAMES:
        expected = xci_signature(out / "ip" / name / (name + ".xci"))
        copies = sorted(directory.rglob(name + ".xci"))
        if not copies:
            raise ValueError("Imported customization missing: " + name)
        for path in copies:
            if xci_signature(path) != expected:
                raise ValueError("Imported arithmetic IP parameters changed: " + str(path))
            evidence.append(dict(path=str(path), sha256=sha(path), configuration_identical=True))
    report = dict(status="PASS", compared_groups=["component_parameters", "model_parameters"],
                  vlnv_revision_identical=True, raw_path_fields_excluded=True, copies=evidence)
    dump(out / (label + "_ip_identity.json"), report)
    return report


def audit_ps(out):
    path = out / "source/verification/system/audit_ps_configuration.py"
    spec = importlib.util.spec_from_file_location("frozen_ps_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = module.DEFAULT_BASELINE / module.REPORT_REL
    if sha(baseline) != module.BASELINE_REPORT_SHA256:
        raise ValueError("Proven PS-only configuration report changed")
    report = module.compare_parameters(module.read_parameters(baseline), module.read_parameters(out / module.REPORT_REL))
    report.update(baseline_report_sha256=sha(baseline), system_report_sha256=sha(out / module.REPORT_REL))
    dump(out / "ps_configuration_audit.json", report)
    if report["status"] != "PASS":
        raise ValueError("PS DDR/MIO/CPU or clock configuration audit failed")
    return report


def verify_frozen(out, manifest):
    for relative, expected in manifest["immutable_files"].items():
        if sha(out / relative) != expected:
            raise ValueError("Frozen input/source changed: " + relative)


def launch(out, name, script, arguments, sentinel, manifest):
    verify_frozen(out, manifest)
    manifest["scheduling_checks"].append(process_guard())
    command = [str(VIVADO), "-mode", "batch", "-notrace", "-log", name + ".log", "-journal", name + ".jou",
               "-source", str(out / "source" / script), "-tclargs", *map(str, arguments)]
    manifest.setdefault("commands", []).append(command)
    dump(out / "run_manifest.json", manifest)
    log_path = out / (name + "_console.log")
    if log_path.exists():
        raise ValueError("Refusing to overwrite a previous tool log: " + str(log_path))
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if result.returncode or lines.count(sentinel) != 1:
        raise RuntimeError(f"{name} did not complete: exit={result.returncode}, success_sentinels={lines.count(sentinel)}")
    verify_frozen(out, manifest)


def verify_simulation(out, manifest):
    result = read(out / "vendor_axi_simulation.json")
    expected = dict(status="PASS", clips=2, samples_per_clip=672, frames_per_clip=2,
                    mfcc_records=52, mismatches=0, abort_partial_PCM=173,
                    native_starts=3, native_ends=2, native_dones=2, actual_vendor_IP=True)
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("Real-IP AXI simulation is incomplete")
    if any(result.get(key, 0) <= 0 for key in ("R_stall_cycles", "B_stall_cycles", "native_stall_cycles")):
        raise ValueError("AXI/native backpressure coverage missing")
    lines = (out / "vendor_axi_simulate.log").read_text(encoding="utf-8", errors="replace").splitlines()
    if lines.count("FP32_VENDOR_AXI_PASS") != 1:
        raise ValueError("Real-IP simulation success sentinel missing/duplicated")
    if any("aresetn must be asserted or deasserted" in line for line in lines):
        raise ValueError("Vendor reset-width warning requires diagnosis before implementation")
    manifest["simulation"] = result
    manifest["validation"]["vendor_AXI_simulation"] = "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("prepare", "sim", "bd", "bitstream", "all"), default="prepare")
    parser.add_argument("--resume", action="store_true", help="advance unchanged prepared/simulated run; never retry failed runs")
    args = parser.parse_args()
    if not args.run_id.replace("_", "").replace("-", "").isalnum():
        parser.error("Use a simple new run ID")
    out = BUILD / "system_fp32" / args.run_id
    if len(str(out)) > 40:
        parser.error("Windows IP checkpoint paths require a short run root, at most 40 characters")
    if args.resume:
        manifest = read(out / "run_manifest.json")
        index = read(out / "artifact_hashes.json")
        if index.get("run_manifest.json") != sha(out / "run_manifest.json"):
            raise ValueError("Resume manifest integrity failed")
        if manifest["status"] not in ("prepared", "simulated", "block_design_prepared"):
            raise ValueError("Only successful prepared/simulated runs may advance")
        verify_frozen(out, manifest)
        # Authenticate reused simulation reports as well as frozen sources.
        if manifest["validation"]["vendor_AXI_simulation"] == "PASS":
            for relative in ("vendor_axi_simulation.json", "vendor_axi_simulate.log", "simulation_ip_identity.json"):
                if index.get(relative) != sha(out / relative):
                    raise ValueError("Resume simulation artifact changed: " + relative)
            verify_simulation(out, manifest)
    else:
        out.mkdir(parents=True, exist_ok=False)
        manifest = dict(status="preparing", started_at_utc=now(), tool="Vivado2024.2", part="xc7z020clg400-1",
                        physical_board_accessed=False, evaluation_audio_used=False, synthetic_scope="not_retested_known_failures_retained",
                        numerical_scope="development_prefix_transport_exact_bits_only", full_development_gate_passed=False,
                        abi=dict(version=0x10001, core_id=2, output_format=0x30020, contract_tag=0xc556a8e8, error_detail_offset=0x64),
                        baseline=dict(continuous=str(GOLD), continuous_index_sha256=GOLD_INDEX_SHA,
                                      synthesis=str(SYNTH), synthesis_index_sha256=SYNTH_INDEX_SHA, ip_manifest_sha256=IP_MANIFEST_SHA),
                        scheduling_checks=[], validation=dict(vendor_AXI_simulation="NOT_RUN", board_implementation="NOT_RUN",
                                                             PS_configuration="NOT_RUN", board_execution="NOT_RUN", PS_bus_model_simulation="NOT_RUN"))
    try:
        if not args.resume:
            manifest["source_sha256"], manifest["board_source"] = snapshot(out)
            manifest["case"] = stage_inputs(out)
            manifest["immutable_files"] = {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*")) if p.is_file()}
            manifest["status"] = "prepared"
            dump(out / "freeze.json", manifest)
        if args.mode in ("sim", "all"):
            launch(out, "simulation", "hardware/system_fp32/simulate_fp32_system.tcl", [out], "FP32_VENDOR_AXI_TCL_SUCCESS", manifest)
            audit_imported_ip(out, out / "sim_project", "simulation")
            verify_simulation(out, manifest)
            manifest["status"] = "simulated"
        if args.mode in ("bd", "bitstream", "all"):
            if args.mode != "bd" and manifest["validation"]["vendor_AXI_simulation"] != "PASS":
                raise ValueError("Real-IP AXI simulation must pass before bitstream implementation")
            if not (out / "design").exists():
                launch(out, "block_design", "hardware/system_fp32/create_fp32_system.tcl",
                       [out, out / "board_source/board_files", "prepare"], "FP32_SYSTEM_TCL_SUCCESS", manifest)
            # The persistent package itself must include all customizations;
            # the temporary IP-packing project alone is insufficient evidence.
            audit_imported_ip(out, out / "design/ip_repo", "packaged")
            manifest["PS_configuration"] = audit_ps(out)
            manifest["validation"]["PS_configuration"] = "PASS"
            manifest["status"] = "block_design_prepared"
        if args.mode in ("bitstream", "all"):
            launch(out, "implementation", "hardware/system_fp32/implement_fp32_system.tcl", [out], "FP32_IMPLEMENTATION_TCL_SUCCESS", manifest)
            audit_imported_ip(out, out / "design", "implemented")
            timing = read(out / "reports/timing.json")
            if set(timing) != {"setup_slack_ns", "hold_slack_ns"} or min(timing.values()) < 0:
                raise ValueError("Full system timing failed")
            bit = out / "design/zybo_z7_20_fp32.bit"
            xsa = out / "design/zybo_z7_20_fp32.xsa"
            with zipfile.ZipFile(xsa) as archive:
                members = archive.namelist()
                bits = [name for name in members if name.endswith(".bit")]
                if len(bits) != 1 or hashlib.sha256(archive.read(bits[0])).hexdigest() != sha(bit):
                    raise ValueError("XSA embedded bitstream differs")
                if sum(name.endswith("ps7_init.tcl") for name in members) != 1:
                    raise ValueError("XSA PS initialization missing/ambiguous")
            manifest["timing"] = timing
            manifest["outputs"] = dict(bitstream=dict(path=str(bit), sha256=sha(bit)),
                                       xsa=dict(path=str(xsa), sha256=sha(xsa), members=members))
            manifest["validation"]["board_implementation"] = "PASS"
            launch(out, "ps_simulation", "hardware/system_fp32/simulate_ps_system.tcl", [out], "FP32_PS_SIMULATION_TCL_SUCCESS", manifest)
            result = read(out / "ps_simulation.json")
            expected = dict(status="PASS", frames=2, samples=672, mfcc_words=26,
                            arm_instructions_executed=False, physical_board_accessed=False,
                            physical_ddr_timing_verified=False)
            if any(result.get(key) != value for key, value in expected.items()):
                raise ValueError("Generated PS7/GP0 FP32 simulation incomplete")
            if any(result.get(key, 0) < minimum for key, minimum in dict(reset_checks=2, error_checks=2, empty_checks=1, abort_checks=1, output_hold_checks=2).items()):
                raise ValueError("Generated PS7/GP0 coverage missing")
            if not 9.99 <= result.get("clock_period_ns", 0) <= 10.01:
                raise ValueError("PS7 simulated FCLK0 is not 100 MHz")
            log = (out / "tb_fp32_ps_system_simulate.log").read_text(encoding="utf-8", errors="replace")
            if sum(line.startswith("FP32_PS_SYSTEM_PASS ") for line in log.splitlines()) != 1:
                raise ValueError("PS7 simulation success sentinel missing/duplicated")
            if "aresetn must be asserted or deasserted" in log:
                raise ValueError("PS7 simulation has an unresolved vendor reset-width warning")
            manifest["ps_simulation"] = result
            manifest["validation"]["PS_bus_model_simulation"] = "PASS"
            manifest["status"] = "complete"
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        raise
    finally:
        manifest["updated_at_utc"] = now()
        dump(out / "run_manifest.json", manifest)
        dump(out / "artifact_hashes.json", {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*"))
                                            if p.is_file() and p.name != "artifact_hashes.json" and ".Xil" not in p.parts and p.suffix not in (".lock", ".lck")})
    print(json.dumps(dict(run=str(out), status=manifest["status"], validation=manifest["validation"]), indent=2))


if __name__ == "__main__":
    sys.exit(main())
