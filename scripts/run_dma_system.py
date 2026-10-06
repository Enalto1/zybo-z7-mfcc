"""Freeze both numeric cores and build one shared DMA/IRQ system variant.

No hardware access. Both variants use identical GP0/HP0/DMA/IRQ configuration.
The generated PS7 VIP + actual DMA/HP test must pass before implementation.
Failed runs require a new short run ID; --resume only advances intact evidence.
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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT.parent / "build"
VIVADO = Path("C:/Xilinx/Vivado/2024.2/bin/vivado.bat")
GOLD = BUILD / "fp32_hw/continuous_development_01"
SYNTH = BUILD / "fp32_hw/synth_compact_01"
IP = BUILD / "fp32_hw/ip_04"
FIXED_GOLD = BUILD / "fixed_full_rtl/full_616_repro_20261004_07"
MODEL = BUILD / "fixed_full_model/integer_02_20261004"
CONTRACT = BUILD / "fixed_contract/v2_pcm16_mfcc40_20261004_r2"
FIXED_RUN_SHA = "6b29205e6d077682c5e5484c3021ef1366ace357fa07208e952c70e3640a1325"
FIXED_INDEX_SHA = "f9c5177e300dc466afa653c4dbbc4f13e09f750a4125dba70d4f1cb759908eb3"
CONTRACT_SHA = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"
ADAPTER_SHA = "f5179aa85d1ddeb15de452aa92007c7c7437acbf0f73dd39ac15922c22c9370e"
TRANSPORT_SHA = "40037009ac13c2eee2ddc1d63ec4c52d2aa1467efced878022fbf7401aec045c"
TRANSPORT_UNIT = BUILD / "system_dma/unit07"
TRANSPORT_UNIT_SHA = "73dfe504b49103c4eee11c09697c6a973b9d56a2245dbe9f6217a8f3f848d7e9"
TRANSPORT_UNIT_INDEX_SHA = "56abe0a0208a6abdc3692f93e1891edfdd3ae53f912a59fc68abd7e39c5f6bfe"
TOP_SHA = "7bc18b2918a7d2996dd22aade80aa4b5d2ffae85584a3d7816bae2c351882299"
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


def fixed_hardware_hashes(source_root):
    """Enumerate the complete candidate, rejecting additions and omissions by set."""
    root = Path(source_root)
    files = sorted((root / "hardware/fixed").rglob("*.sv")) + sorted((root / "hardware/fixed").rglob("*.mem"))
    return {p.relative_to(root).as_posix(): sha(p) for p in files}


def verify_fixed_evidence(evidence, source_root=None):
    """Authenticate a completed full-corpus candidate, never a smoke-only waiver.

    The original dense baseline remains pinned. Candidate stage vectors must be
    byte-identical to its oracle vectors; only implementation sources may differ.
    This helper also verifies the copied evidence embedded in a system snapshot.
    """
    evidence = Path(evidence).resolve()
    if evidence.is_dir():
        evidence = evidence / "run_manifest.json"
    if evidence.name != "run_manifest.json":
        raise ValueError("Fixed evidence must name a full RTL run_manifest.json")
    root = evidence.parent
    candidate = read(evidence)
    index = read(root / "artifact_hashes.json")
    if sha(FIXED_GOLD / "run_manifest.json") != FIXED_RUN_SHA or sha(FIXED_GOLD / "artifact_hashes.json") != FIXED_INDEX_SHA:
        raise ValueError("Pinned full fixed baseline changed")
    baseline = read(FIXED_GOLD / "run_manifest.json")
    baseline_index = read(FIXED_GOLD / "artifact_hashes.json")
    if candidate.get("status") != "complete" or candidate.get("full_corpus") is not True or candidate.get("frames") != 616:
        raise ValueError("Fixed candidate requires completed full616 corpus evidence")
    if candidate.get("vivado_exit_code") != 0:
        raise ValueError("Fixed candidate lacks a successful recorded Vivado exit code")
    if candidate.get("cases") != baseline["cases"] or candidate.get("model_manifest_sha256") != baseline["model_manifest_sha256"]:
        raise ValueError("Fixed candidate cases or integer oracle differ")
    if candidate.get("c_contract", {}).get("sha256") != CONTRACT_SHA or candidate.get("c_contract", {}).get("publication_sha256") != baseline["c_contract"]["publication_sha256"]:
        raise ValueError("Fixed candidate arithmetic contract differs")
    if candidate.get("tool") != "Vivado2024.2" or candidate.get("part") != "xc7z020clg400-1" or candidate.get("clock_ns") != 10:
        raise ValueError("Fixed candidate tool/part/clock differs")
    if candidate.get("validation_status", {}).get("simulation") != "PASS":
        raise ValueError("Fixed candidate simulation gate failed")
    if candidate.get("accuracy_accepted") is not False or candidate.get("validation_status", {}).get("numerical_accuracy") != "NOT_ACCEPTED":
        raise ValueError("Structural optimization must preserve the fixed numerical acceptance status")
    protocol = read(root / "protocol.json")
    expected = dict(status="PASS", cases=24, frames=616, front_samples=315392,
                    fft_complex_bins=315392, power_bins=158312, mel_values=16016,
                    log_values=16016, mfcc_values=8008, mismatches=0,
                    reset_partial_pcm=173, reset_fft_inflight=True)
    if any(protocol.get(k) != v for k, v in expected.items()) or protocol.get("stall_cycles", 0) <= 0:
        raise ValueError("Fixed candidate bit-match/protocol coverage incomplete")
    if candidate.get("simulation") != protocol:
        raise ValueError("Fixed candidate manifest/protocol binding differs")
    sources = candidate.get("source_sha256", {})
    hardware = {k: v for k, v in sources.items() if k.startswith("hardware/fixed/") and Path(k).suffix in (".sv", ".mem")}
    if not hardware or fixed_hardware_hashes(root / "source") != hardware:
        raise ValueError("Fixed candidate snapshot hardware source set/hash differs")
    if source_root is not None and fixed_hardware_hashes(source_root) != hardware:
        raise ValueError("Current/system fixed hardware differs from validated candidate")
    # Dense coefficients and FFT twiddles are preserved byte for byte. The only
    # additional coefficient ROM presently supported is the compact Mel table.
    baseline_mem = {k: v for k, v in baseline["source_sha256"].items() if k.startswith("hardware/fixed/") and k.endswith(".mem")}
    candidate_mem = {k: v for k, v in hardware.items() if k.endswith(".mem")}
    allowed_mem = set(baseline_mem) | {"hardware/fixed/power_mel/mel_sparse_fw16.mem"}
    if not set(candidate_mem) <= allowed_mem or any(candidate_mem.get(k) != v for k, v in baseline_mem.items()):
        raise ValueError("Fixed candidate original/compact coefficient ROM identity differs")
    required = {"run_manifest.json", "protocol.json"}
    for relative, digest in sources.items():
        path = (root / "source" / relative).resolve()
        if not path.is_relative_to((root / "source").resolve()) or sha(path) != digest:
            raise ValueError("Fixed candidate source changed: " + relative)
        required.add("source/" + relative)
    for name in ("pcm.mem", "front.mem", "fft.mem", "power.mem", "mel.mem", "log.mem", "mfcc.mem", "shift.mem", "cases.txt"):
        if sha(root / name) != baseline_index[name] or sha(FIXED_GOLD / name) != baseline_index[name]:
            raise ValueError("Fixed candidate full-corpus oracle vector changed: " + name)
        required.add(name)
    for relative in required:
        if index.get(relative) != sha(root / relative):
            raise ValueError("Fixed candidate artifact index differs: " + relative)
    return dict(kind="validated_full_corpus_fixed_candidate", manifest_sha256=sha(evidence),
                artifact_index_sha256=sha(root / "artifact_hashes.json"), protocol_sha256=sha(root / "protocol.json"),
                source_sha256=hardware, frames=616, cases=24, mismatches=0,
                files={name: sha(root / name) for name in sorted(required)})


def stage_fixed_evidence(out, evidence, fixed_source_root=ROOT):
    source = Path(evidence).resolve()
    if source.is_file():
        source = source.parent
    binding = verify_fixed_evidence(source, fixed_source_root)
    target = out / "provenance/fixed_evidence"
    for relative in [*binding["files"], "artifact_hashes.json"]:
        copy(source / relative, target / relative)
    if verify_fixed_evidence(target, fixed_source_root) != binding:
        raise ValueError("Fixed evidence changed during snapshot")
    return binding


def snapshot(out, fixed_evidence=None, fixed_source_root=ROOT):
    fixed_source_root = Path(fixed_source_root).resolve()
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
    if sha(FIXED_GOLD / "run_manifest.json") != FIXED_RUN_SHA or sha(FIXED_GOLD / "artifact_hashes.json") != FIXED_INDEX_SHA:
        raise ValueError("Verified fixed core baseline identity changed")
    fixed = read(FIXED_GOLD / "run_manifest.json")
    fi = read(FIXED_GOLD / "artifact_hashes.json")
    if fixed["status"] != "complete":
        raise ValueError("Fixed full-core baseline incomplete")
    fixed_sources = sorted((fixed_source_root / "hardware/fixed").rglob("*.sv")) + sorted((fixed_source_root / "hardware/fixed").rglob("*.mem"))
    if fixed_evidence is not None:
        verify_fixed_evidence(out / "provenance/fixed_evidence", fixed_source_root)
    else:
        for source in fixed_sources:
            relative = source.relative_to(fixed_source_root).as_posix()
            if sha(source) != fixed["source_sha256"].get(relative):
                raise ValueError("Verified fixed arithmetic changed: " + relative)
            verify_indexed(FIXED_GOLD, fi, "source/" + relative)
    adapter = ROOT / "hardware/system_fp32/fp32_core_adapter.sv"
    if sha(adapter) != ADAPTER_SHA:
        raise ValueError("FP32 reset adapter differs from verified low16/high4 revision")
    dma_sources = sorted((ROOT / "hardware/system_dma").glob("*.sv"))
    if not dma_sources or not (ROOT / "hardware/system_dma/mfcc_dma_top.sv").is_file():
        raise ValueError("DMA transport RTL is not ready")
    if sha(ROOT / "hardware/system_dma/mfcc_dma_top.sv") != TOP_SHA or sha(ROOT / "hardware/system_dma/mfcc_dma_transport.sv") != TRANSPORT_SHA:
        raise ValueError("DMA transport differs from the unit06-verified revision")
    if sha(TRANSPORT_UNIT / "unit_manifest.json") != TRANSPORT_UNIT_SHA or sha(TRANSPORT_UNIT / "artifact_hashes.json") != TRANSPORT_UNIT_INDEX_SHA:
        raise ValueError("Reviewed PREP transport unit evidence changed")
    if read(TRANSPORT_UNIT / "unit_manifest.json")["status"] != "PASS":
        raise ValueError("Reviewed PREP transport units did not pass")
    paths += fixed_sources + [adapter] + dma_sources
    paths += sorted((ROOT / "hardware/system_dma").glob("*.tcl"))
    paths += sorted((ROOT / "verification/system_dma").glob("*.sv"))
    paths += sorted((ROOT / "verification/system_dma").glob("*.py"))
    paths += [Path(__file__), ROOT / "scripts/run_vivado_generated_worker.py",
              ROOT / "scripts/recover_dma_vivado_workers.py",
              ROOT / "hardware/system_dma/REGISTER_CONTRACT.md",
              ROOT / "hardware/platform/board_source.json", ROOT / "AGENTS.md",
              ROOT / "docs/RTL_CODING_RULES.md", ROOT / "docs/MFCC_SPEC.md",
              ROOT / "hardware/fp32/ip/IP_CONTRACT.md", ROOT / "verification/c/tolerances.json",
              ROOT / "hardware/fixed/fft/PROVENANCE.json"]
    hashes = {}
    for source in sorted(set(paths)):
        relative = source.relative_to(fixed_source_root if source in fixed_sources else ROOT).as_posix()
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
    rtl = [out / "source" / relative for relative in hashes
           if relative.endswith(".sv") and relative.startswith("hardware/")]
    memories = [out / "source" / relative for relative in hashes
                if relative.endswith(".mem") and relative.startswith("hardware/fixed/")]
    memories += sorted((out / "coefficients").glob("*.mem"))
    expected_fp32 = {"dct_cosine.mem", "dct_scale.mem", "mel.mem", "mel_compact.mem", "mel_descriptor.mem", "window.mem"}
    expected_fixed = {Path(relative).name for relative in hashes if relative.startswith("hardware/fixed/") and relative.endswith(".mem")}
    if set(synth["coefficient_file_hashes"]) != expected_fp32 or len(expected_fixed) != sum(p.suffix == ".mem" for p in fixed_sources):
        raise ValueError("Unexpected or duplicate coefficient memory basename")
    if len(memories) != len(expected_fp32 | expected_fixed) or {p.name for p in memories} != expected_fp32 | expected_fixed:
        raise ValueError("Frozen fixed/FP32 coefficient memory set differs")
    fixed_mel_init = "mel_sparse_fw16.mem" if fixed_evidence is not None and "mel_sparse_fw16.mem" in expected_fixed else "mel_fw16.mem"
    (out / "fixed_mel_init_file.txt").write_text(fixed_mel_init + "\n", encoding="ascii")
    (out / "rtl_files.txt").write_text("\n".join(p.as_posix() for p in sorted(rtl + memories)) + "\n", encoding="utf-8")
    copy(FIXED_GOLD / "run_manifest.json", out / "provenance/fixed_manifest.json")
    copy(CONTRACT / "contract.json", out / "provenance/fixed_contract.json")
    copy(TRANSPORT_UNIT / "unit_manifest.json", out / "provenance/transport_unit_manifest.json")
    copy(TRANSPORT_UNIT / "artifact_hashes.json", out / "provenance/transport_unit_artifact_hashes.json")
    return hashes, spec


def stage_inputs(out, variant):
    gi = authenticated_index(GOLD, GOLD_INDEX_SHA)
    pcm_path = BUILD / "python_reference/reproduce_01/development/8463-294828-0037/input_s16le.pcm"
    if sha(pcm_path) != PCM_SHA or pcm_path.stat().st_size != 85920 * 2:
        raise ValueError("Unmodified development PCM identity/count changed")
    pcm_bytes = pcm_path.read_bytes()[:672 * 2]
    (out / "pcm.mem").write_text("".join(f"{word:04x}\n" for (word,) in struct.iter_unpack("<H", pcm_bytes)), encoding="ascii")
    if b"".join(struct.pack("<H", int(line, 16)) for line in (out / "pcm.mem").read_text().splitlines()) != pcm_bytes:
        raise ValueError("PCM transport vector changed bits")
    records = []
    if variant == "fp32":
        relative = "arrays/8463-294828-0037/"
        meta = read(verify_indexed(GOLD, gi, relative + "arrays.json"))["arrays"]["mfcc"]
        reference = verify_indexed(GOLD, gi, relative + "mfcc.bin")
        if meta["shape"] != [534, 13] or meta["dtype"] != "<f4" or not meta["finite"] or sha(reference) != meta["sha256"] or reference.stat().st_size != 534 * 13 * 4:
            raise ValueError("Preserved FP32 reference metadata changed")
        values = [word for (word,) in struct.iter_unpack("<I", reference.read_bytes()[:26 * 4])]
        for item, value in enumerate(values):
            records += [value, 0, item // 13, item % 13, 0, int(item % 13 == 12)]
    else:
        if sha(CONTRACT / "contract.json") != CONTRACT_SHA:
            raise ValueError("Immutable fixed numeric contract changed")
        contract = read(CONTRACT / "contract.json")
        if sha(MODEL / "run_manifest.json") != contract["source_run"]["sha256"]:
            raise ValueError("Fixed integer model identity changed")
        model = read(MODEL / "run_manifest.json")
        cases = [c for c in model["cases"] if c["group"] == "development" and c["id"] == "8463-294828-0037"]
        if model["status"] != "complete" or len(cases) != 1:
            raise ValueError("Fixed development reference incomplete")
        case = cases[0]
        reference = MODEL / case["integer_file"]
        if sha(reference) != case["sha256"] or case["pcm"]["sha256"] != PCM_SHA or case["frames"] != 534:
            raise ValueError("Fixed reference/PCM provenance changed")
        with np.load(reference, allow_pickle=False) as values:
            mfcc = values["mfcc_q24"]
            shifts = values["shift_s"]
            if mfcc.shape != (534, 13) or len(shifts) != 534:
                raise ValueError("Fixed reference shape changed")
            for item, value in enumerate(mfcc[:2].ravel()):
                value = int(value)
                if not -(1 << 39) <= value < (1 << 39):
                    raise ValueError("Fixed payload is outside signed40")
                bfp = int(shifts[item // 13])
                if not -128 <= bfp <= 127:
                    raise ValueError("Fixed BFP metadata is outside signed8")
                records += [value & 0xffffffff, (value >> 32) & 0xffffffff,
                            item // 13, item % 13, bfp & 0xffffffff, int(item % 13 == 12)]
    (out / "records.mem").write_text("".join(f"{word:08x}\n" for word in records), encoding="ascii")
    cases = [0, 511, 512, 671, 672, 672]
    return dict(id="8463-294828-0037", samples=cases, frames=[0, 0, 1, 1, 2, 2],
                compared_records=78, first_sample=0, original_pcm_sha256=PCM_SHA,
                selected_pcm_sha256=hashlib.sha256(pcm_bytes).hexdigest(), reference=str(reference),
                reference_sha256=sha(reference), exact_bits_required=True, metadata_mismatches_allowed=0,
                malformed_DMA_one_byte_transfer=True)


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


def audit_configuration(out):
    path = out / "source/verification/system_dma/audit_configuration.py"
    spec = importlib.util.spec_from_file_location("frozen_dma_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(out)
    if report["status"] == "PASS":
        try:
            report["fixed_mel_init_file"] = verify_mel_binding(out, read(out / "run_manifest.json"))
        except ValueError as error:
            report["status"] = "FAIL"
            report["failures"].append(str(error))
    dump(out / "configuration_audit.json", report)
    if report["status"] != "PASS":
        raise ValueError("PS/DMA/HP/IRQ/address/variant configuration audit failed")
    return report


def verify_mel_binding(out, manifest):
    """Check the actual BD parameter, not only the leaf RTL's default string."""
    expected = "mel_fw16.mem"
    if "fixed_evidence" in manifest:
        if "hardware/fixed/power_mel/mel_sparse_fw16.mem" in manifest["fixed_evidence"]["source_sha256"]:
            expected = "mel_sparse_fw16.mem"
        if manifest.get("fixed_mel_init_file") != expected:
            raise ValueError("Validated candidate ROM selection is not pinned in the system manifest")
    if "fixed_mel_init_file" in manifest:
        if manifest["fixed_mel_init_file"] != expected or (out / "fixed_mel_init_file.txt").read_text(encoding="ascii").strip() != expected:
            raise ValueError("Frozen fixed Mel initialization selection differs")
    rows = (out / "reports/accelerator_parameters.tsv").read_text(encoding="utf-8").splitlines()
    actual = [line.split("\t", 1)[1] for line in rows if line.startswith("CONFIG.MEL_INIT_FILE\t")]
    if actual != [expected]:
        raise ValueError("Actual packaged fixed Mel ROM parameter differs from validated RTL")
    return expected


def verify_frozen(out, manifest):
    for relative, expected in manifest["immutable_files"].items():
        if sha(out / relative) != expected:
            raise ValueError("Frozen input/source changed: " + relative)
    if "fixed_evidence" in manifest:
        binding = verify_fixed_evidence(out / "provenance/fixed_evidence", out / "source")
        if binding != manifest["fixed_evidence"]:
            raise ValueError("Frozen fixed candidate evidence binding changed")


def model_module(out):
    spec = importlib.util.spec_from_file_location("frozen_ps_vip_model", out / "source/verification/system_dma/ps_vip_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolation_module(out):
    spec = importlib.util.spec_from_file_location("frozen_sim_library_isolation", out / "source/verification/system_dma/sim_library_isolation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compile_simulation_model(out, manifest):
    """Compile explicitly: Vivado otherwise omits this vendor library's source."""
    module = model_module(out)
    module.verify(out, manifest["simulation_model"])
    manifest["scheduling_checks"].append(process_guard())
    root = out / "simulation_model"
    library = root / "library"
    command = ["C:/Xilinx/Vivado/2024.2/bin/xvlog.bat", "--sv", "--relax",
               "-L", "uvm", "-L", "axi_vip_v1_1_19", "-L", "xilinx_vip",
               "--initfile", (root / "local_xsim.ini").as_posix(),
               "--work", "processing_system7_vip_v1_0_21=" + library.as_posix(),
               "-i", (root / "patched").as_posix(), (root / "patched" / module.SOURCE).as_posix(),
               "-log", "model_compile.log"]
    log_path = out / "model_compile_console.log"
    if log_path.exists():
        raise ValueError("Refusing simulation model compilation log overwrite")
    # Windows batch launchers split an unquoted '=' in --work; a tool option
    # file preserves the exact logical=physical mapping through the launcher.
    options = out / "model_compile_options.txt"
    options.write_text("\n".join('"' + arg + '"' for arg in command[1:]) + "\n", encoding="utf-8")
    invoked = [command[0], "-f", options.as_posix()]
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(invoked, cwd=out, stdout=log, stderr=subprocess.STDOUT)
    files = {p.name: sha(p) for p in library.iterdir() if p.is_file()}
    report = dict(command=command, invoked=invoked, options_sha256=sha(options), exit_code=result.returncode,
                  physical_library=library.as_posix(), files=files)
    dump(out / "simulation_model_compile.json", report)
    module.verify(out, manifest["simulation_model"])
    if result.returncode or "processing_system7_vip_v1_0_21_arb_wr_4.sdb" not in files:
        raise RuntimeError("Explicit run-local PS VIP compilation did not complete")


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
    manifest.setdefault("tool_results", []).append(dict(stage=name, command=command, exit_code=result.returncode,
                                                        success_sentinels=lines.count(sentinel), log_sha256=sha(log_path)))
    dump(out / "run_manifest.json", manifest)
    if result.returncode or lines.count(sentinel) != 1:
        raise RuntimeError(f"{name} did not complete: exit={result.returncode}, success_sentinels={lines.count(sentinel)}")
    verify_frozen(out, manifest)


def verify_simulation(out, manifest):
    model_module(out).verify(out, manifest["simulation_model"])
    isolation = isolation_module(out).verify(out)
    if isolation != manifest.get("compile_library_isolation"):
        raise ValueError("All-library isolation manifest binding differs")
    project_file = out / "design/vivado_project/zybo_dma.sim/sim_1/behav/xsim/tb_dma_system_vlog.prj"
    compiled = read(out / "simulation_model_compile.json")
    library = out / "simulation_model/library"
    if compiled["exit_code"] != 0 or compiled["physical_library"] != library.as_posix():
        raise ValueError("Explicit run-local VIP compilation record invalid")
    if "processing_system7_vip_v1_0_21=" + library.as_posix() not in compiled["command"]:
        raise ValueError("Explicit physical VIP compile target missing")
    if sha(out / "model_compile_options.txt") != compiled["options_sha256"]:
        raise ValueError("Explicit VIP compiler option file changed")
    for name, expected in compiled["files"].items():
        if sha(library / name) != expected:
            raise ValueError("Compiled simulation model changed: " + name)
    sim = project_file.parent
    initfile = isolation["initfile"]
    for name in ("compile.bat", "elaborate.bat"):
        command = (sim / name).read_text(encoding="utf-8").replace("\\", "/")
        if "--initfile" not in command or initfile not in command:
            raise ValueError("Explicit local VIP mapping missing from " + name)
    if not (out / "simulation_model/library/processing_system7_vip_v1_0_21_arb_wr_4.sdb").is_file():
        raise ValueError("Corrected VIP physical library missing")
    result = read(out / "dma_simulation.json")
    expected = dict(status="PASS", clips=6, samples=3038, frames=6,
                    mfcc_records=78, record_word_mismatches=0,
                    actual_AXI_DMA=True, PS_HP0_DDR_model=True,
                    arm_instructions_executed=False, physical_board_accessed=False,
                    malformed_input_error_checks=1, output_canary_checks=6,
                    partial_input_abort_checks=1,
                    core_kind=manifest["core_kind"])
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("Generated PS/actual DMA/core integration incomplete")
    if not 173 <= result.get("partial_abort_consumed", 0) < 512:
        raise ValueError("Actual partial-input DMA/core abort coverage missing")
    if result.get("irq_mm2s_completions") != 5 or result.get("irq_s2mm_completions") != 4 or result.get("irq_core_completions") != 6:
        raise ValueError("DMA/core interrupt coverage incomplete")
    if not 9.99 <= result.get("clock_period_ns", 0) <= 10.01:
        raise ValueError("PS7 simulated FCLK0 is not 100 MHz")
    lines = (out / "dma_simulate.log").read_text(encoding="utf-8", errors="replace").splitlines()
    if sum(line.startswith("DMA_SYSTEM_PASS ") for line in lines) != 1:
        raise ValueError("DMA simulation success sentinel missing/duplicated")
    if any("aresetn must be asserted or deasserted" in line for line in lines):
        raise ValueError("Vendor reset-width warning requires diagnosis before implementation")
    manifest["simulation"] = result
    manifest["validation"]["actual_DMA_PS_simulation"] = "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--variant", choices=("fixed", "fp32"), required=True)
    parser.add_argument("--mode", choices=("prepare", "bd", "sim", "bitstream", "all"), default="prepare")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fixed-evidence", type=Path,
                        help="Opt in to a completed full616 bit-match run manifest for changed fixed RTL")
    parser.add_argument("--fixed-source-root", type=Path,
                        help="Read fixed RTL/ROM from this authenticated source snapshot instead of the live tree; requires --fixed-evidence")
    args = parser.parse_args()
    if args.fixed_evidence is not None and args.variant != "fixed":
        parser.error("Candidate fixed evidence is only supported for --variant fixed")
    if args.fixed_source_root is not None and (args.fixed_evidence is None or args.resume):
        parser.error("--fixed-source-root requires --fixed-evidence on a new run")
    if not args.run_id.replace("_", "").replace("-", "").isalnum():
        parser.error("Use a simple new run ID")
    out = BUILD / "system_dma" / args.run_id
    if len(str(out)) > 40:
        parser.error("Windows IP checkpoint paths require a run root of at most40characters")
    if args.resume:
        manifest = read(out / "run_manifest.json")
        index = read(out / "artifact_hashes.json")
        if index.get("run_manifest.json") != sha(out / "run_manifest.json"):
            raise ValueError("Resume manifest integrity failed")
        if manifest["variant"] != args.variant or manifest["status"] not in ("prepared", "simulated", "block_design_prepared"):
            raise ValueError("Only successful same-variant prepared/simulated runs may advance")
        verify_frozen(out, manifest)
        if args.fixed_evidence is not None and verify_fixed_evidence(args.fixed_evidence) != manifest.get("fixed_evidence"):
            raise ValueError("Resume fixed evidence differs from the pinned candidate")
        if manifest["validation"]["actual_DMA_PS_simulation"] == "PASS":
            for relative in ("dma_simulation.json", "dma_simulate.log", "configuration_audit.json", "packaged_ip_identity.json"):
                if index.get(relative) != sha(out / relative):
                    raise ValueError("Resume simulation artifact changed: " + relative)
            verify_simulation(out, manifest)
    else:
        out.mkdir(parents=True, exist_ok=False)
        core_kind = 1 if args.variant == "fixed" else 2
        manifest = dict(status="preparing", started_at_utc=now(), tool="Vivado2024.2", part="xc7z020clg400-1",
                        variant=args.variant, core_kind=core_kind,
                        physical_board_accessed=False, evaluation_audio_used=False,
                        synthetic_scope="not_retested_known_failures_retained", fixed_float64_accuracy="NOT_ACCEPTED",
                        numerical_scope="development_prefix_transport_exact_bits_only", full_development_gate_passed=False,
                        abi=dict(version=0x20000, core_id=core_kind,
                                 output_format=0x11828 if core_kind == 1 else 0x30020,
                                 contract_tag=0x283fff8a if core_kind == 1 else 0xc556a8e8,
                                 record_bytes=24, dma_base=0x40400000, core_base=0x43c00000,
                                 IRQ_F2P=dict(mm2s=0, s2mm=1, core=2)),
                        baseline=dict(continuous=str(GOLD), continuous_index_sha256=GOLD_INDEX_SHA,
                                      synthesis=str(SYNTH), synthesis_index_sha256=SYNTH_INDEX_SHA,
                                      ip_manifest_sha256=IP_MANIFEST_SHA, fixed=str(FIXED_GOLD),
                                      fixed_manifest_sha256=FIXED_RUN_SHA, fixed_contract_sha256=CONTRACT_SHA,
                                      transport_unit_run=str(TRANSPORT_UNIT), transport_unit_manifest_sha256=TRANSPORT_UNIT_SHA,
                                      transport_unit_index_sha256=TRANSPORT_UNIT_INDEX_SHA),
                        scheduling_checks=[], validation=dict(actual_DMA_PS_simulation="NOT_RUN", board_implementation="NOT_RUN",
                                                             configuration="NOT_RUN", board_execution="NOT_RUN"))
    try:
        if not args.resume:
            fixed_source_root = args.fixed_source_root.resolve() if args.fixed_source_root is not None else ROOT
            if args.fixed_evidence is not None:
                manifest["fixed_evidence"] = stage_fixed_evidence(out, args.fixed_evidence, fixed_source_root)
                manifest["fixed_evidence_origin"] = str(args.fixed_evidence.resolve())
                manifest["fixed_source_root"] = str(fixed_source_root)
            manifest["source_sha256"], manifest["board_source"] = snapshot(out, manifest.get("fixed_evidence"), fixed_source_root)
            manifest["fixed_mel_init_file"] = (out / "fixed_mel_init_file.txt").read_text(encoding="ascii").strip()
            if "fixed_evidence" in manifest and verify_fixed_evidence(out / "provenance/fixed_evidence", out / "source") != manifest["fixed_evidence"]:
                raise ValueError("Fixed source changed during system snapshot")
            manifest["simulation_model"] = model_module(out).stage(out)
            manifest["case"] = stage_inputs(out, args.variant)
            manifest["immutable_files"] = {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*")) if p.is_file()}
            manifest["status"] = "prepared"
            dump(out / "freeze.json", manifest)
        if args.mode != "prepare":
            if not (out / "design").exists():
                launch(out, "block_design", "hardware/system_dma/create_dma_system.tcl",
                       [out, out / "board_source/board_files", args.variant], "DMA_SYSTEM_TCL_SUCCESS", manifest)
            audit_imported_ip(out, out / "design/ip_repo", "packaged")
            # Audit reads the variant and frozen source identities from this manifest.
            dump(out / "run_manifest.json", manifest)
            manifest["configuration"] = audit_configuration(out)
            manifest["validation"]["configuration"] = "PASS"
            manifest["status"] = "block_design_prepared"
        if args.mode in ("sim", "all"):
            isolation = isolation_module(out)
            isolation.prepare(out)
            launch(out, "simulation_prepare", "hardware/system_dma/simulate_dma_system.tcl",
                   [out, args.variant, "prepare"], "DMA_SIMULATION_PREPARE_TCL_SUCCESS", manifest)
            manifest["compile_library_isolation"] = isolation.stage(out)
            compile_simulation_model(out, manifest)
            try:
                launch(out, "simulation", "hardware/system_dma/simulate_dma_system.tcl",
                       [out, args.variant, "simulate"], "DMA_SIMULATION_TCL_SUCCESS", manifest)
            finally:
                isolation.verify(out)
                manifest["compile_library_cache_guard"] = dict(status="PASS", checked_at_utc=now(),
                    libraries=list(manifest["compile_library_isolation"]["installed_caches"]))
                dump(out / "compile_library_cache_guard.json", manifest["compile_library_cache_guard"])
            verify_simulation(out, manifest)
            manifest["status"] = "simulated"
        if args.mode in ("bitstream", "all"):
            if manifest["validation"]["actual_DMA_PS_simulation"] != "PASS":
                raise ValueError("Actual DMA/PS/HP/core simulation must pass before implementation")
            launch(out, "implementation", "hardware/system_dma/implement_dma_system.tcl",
                   [out, args.variant], "DMA_IMPLEMENTATION_TCL_SUCCESS", manifest)
            audit_imported_ip(out, out / "design", "implemented")
            timing = read(out / "reports/timing.json")
            if set(timing) != {"setup_slack_ns", "hold_slack_ns"} or min(timing.values()) < 0:
                raise ValueError("Full system timing failed")
            bit = out / ("design/mfcc_dma_" + args.variant + ".bit")
            xsa = out / ("design/mfcc_dma_" + args.variant + ".xsa")
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
