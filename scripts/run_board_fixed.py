"""Prepare or run the frozen full fixed v2 ARM development clip on ZYBO CPU0.

Offline by default. Execution requires explicit observed cable/target identity.
The runner never starts a hardware server, programs flash, or changes boot media.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import struct
import subprocess
import sys
import traceback
import zipfile

import numpy as np

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "verification/arm"))
from jtag import selection, word, memory_file, reset_initialization
from clocks import expected_configuration, verify_configuration
from uart import Capture

CONTRACT_HASH = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"
PCM_HASH = "026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32"
MAGIC, SAMPLES, FRAMES = 0x43464632, 85920, 534
RESET_POLICY = "Before each ELF: rst -system -stop; halt unique same-cable CPU1; reselect CPU0 without reconnect; same-XSA ps7_init/ps7_post_config"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def process(argv, log, timeout=60):
    argv = [str(x) for x in argv]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
        text = result.stdout + "\n" + result.stderr
        Path(log).write_text("ARGV " + json.dumps(argv) + "\n" + text + f"\nEXIT {result.returncode}\n", encoding="utf-8")
        if result.returncode:
            raise RuntimeError("Command failed: " + str(log))
        return result.stdout
    except subprocess.TimeoutExpired as exc:
        def decode(v):
            return v.decode(errors="replace") if isinstance(v, bytes) else v or ""
        Path(log).write_text("ARGV " + json.dumps(argv) + "\n" + decode(exc.stdout) + "\n" + decode(exc.stderr)
                             + f"\nTIMEOUT {timeout}\n", encoding="utf-8")
        raise


def check_index(root, index):
    entries = read(index)
    for rel, digest in entries.items():
        p = (root / rel).resolve()
        if not p.is_relative_to(root.resolve()) or sha(p) != digest:
            raise ValueError("Artifact hash mismatch: " + str(p))
    return len(entries)


def elf_bytes(path, address, length):
    data = path.read_bytes()
    if data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("Expected little-endian ELF32")
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if header[1] != 40:
        raise ValueError("Expected ARM ELF")
    for i in range(header[9]):
        kind, offset, va, _, size, _, _, _ = struct.unpack_from("<8I", data, header[4] + i * header[8])
        if kind == 1 and va <= address and address + length <= va + size:
            start = offset + address - va
            return data[start:start + length]
    raise ValueError("ELF bytes not wholly in one load segment")


def dwarf_layout(gdb, elf, name, out):
    text = process([gdb, "-nx", "-nh", "-batch", elf, "-ex", "ptype /o " + name], out / (name + "_dwarf.log"))
    fields = {}
    for off, size, field in re.findall(r"/\*\s*(\d+)\s*\|\s*(\d+)\s*\*/\s+[^;\n]*?\b(\w+)(?:\[\d+\])*;", text):
        fields[field] = {"offset": int(off), "bytes": int(size)}
    total = re.search(r"total size \(bytes\):\s*(\d+)", text)
    if not fields or not total:
        raise ValueError("Could not decode actual ELF DWARF: " + name)
    return {"bytes": int(total.group(1)), "fields": fields}


def prepare(args, out):
    build_path = args.build_manifest.resolve()
    arm = build_path.parent
    build = read(build_path)
    if build["status"] != "built_not_board_verified" or build["pc_binding"]["pc_status"] != "passed":
        raise ValueError("Expected successful frozen ARM build / PC bit verification")
    if build["pc_binding"]["published_contract_sha256"] != CONTRACT_HASH or sha(args.contract / "contract.json") != CONTRACT_HASH:
        raise ValueError("Wrong published fixed v2 contract")
    counts = {"arm": check_index(arm, arm / "artifact_manifest.json"),
              "contract": check_index(args.contract, args.contract / "artifact_hashes.json")}
    contract = read(args.contract / "contract.json")
    dev = next(c for c in contract["cases"] if c["group"] == "development")
    if dev["id"] != "8463-294828-0037" or dev["frames"] != FRAMES or dev["pcm"]["sha256"] != PCM_HASH:
        raise ValueError("Unexpected development clip")
    def stage(name):
        e = dev["stages"][name]
        return np.fromfile(args.contract / e["file"], dtype=e["dtype"]).reshape(e["shape"])
    shift = stage("shift_s")
    meta = np.column_stack((np.arange(FRAMES), stage("frame_starts"), shift, -27 - 2*shift,
                           stage("mel_exponent"), stage("fft_overflow"), stage("input_clips"), stage("bfp_clamped"))).astype("<i4")
    expected = {"c_fixed_full_dev_pcm": (args.contract / dev["pcm"]["file"]).read_bytes(),
                "c_fixed_full_dev_mfcc": stage("mfcc_q24").astype("<i8").tobytes(),
                "c_fixed_full_dev_metadata": meta.tobytes()}
    if hashlib.sha256(expected["c_fixed_full_dev_pcm"]).hexdigest() != PCM_HASH:
        raise ValueError("PCM content hash mismatch")
    toolbin = Path(build["compiler"]["path"]).parent
    nm, gdb = toolbin / "arm-none-eabi-nm.exe", toolbin / "arm-none-eabi-gdb.exe"
    binaries = {}
    for mode in ("validate", "timing"):
        dest = out / mode
        dest.mkdir()
        entry = build["binaries"]["c_fixed_full_" + mode]
        elf = Path(entry["path"])
        if sha(elf) != entry["sha256"]:
            raise ValueError("ELF hash mismatch")
        nmtext = process([nm, "-n", "-S", elf], dest / "nm.log")
        symbols = {}
        for line in nmtext.splitlines():
            m = re.fullmatch(r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\w\s+(\S+)", line)
            if m:
                symbols[m[3]] = {"address": int(m[1], 16), "bytes": int(m[2], 16)}
        for name, pinned in entry["symbol_sizes"].items():
            if symbols[name] != {"address": int(pinned["address"], 16), "bytes": pinned["bytes"]}:
                raise ValueError("Actual ELF symbol differs from manifest: " + name)
        for name, value in expected.items():
            s = symbols[name]
            if s["bytes"] != len(value) or elf_bytes(elf, s["address"], s["bytes"]) != value:
                raise ValueError("Embedded fixture differs from published contract: " + name)
        layouts = {name: dwarf_layout(gdb, elf, name, dest) for name in
                   ("arm_cf_full_status", "arm_cf_full_record", "cf_full_state", "cf_full_output", "cf_result")}
        if layouts["arm_cf_full_record"]["bytes"] * FRAMES != symbols["arm_c_fixed_full_results"]["bytes"]:
            raise ValueError("Result array size differs from actual record type")
        if layouts["arm_cf_full_status"]["bytes"] != symbols["arm_c_fixed_full_status"]["bytes"]:
            raise ValueError("Status symbol/type size mismatch")
        for required in ("arm_cf_full_ready", "arm_cf_full_finished"):
            if required not in symbols:
                raise ValueError("Required breakpoint symbol missing")
        binaries[mode] = {"path": str(elf), "sha256": sha(elf), "symbols": symbols, "layouts": layouts}
    init = arm / "ps7_init.tcl"
    xsa = Path(build["reused_platform"]["xsa"])
    if sha(xsa) != build["reused_platform"]["xsa_sha256"]:
        raise ValueError("Platform XSA hash mismatch")
    with zipfile.ZipFile(xsa) as archive:
        names = [n for n in archive.namelist() if Path(n).name == "ps7_init.tcl"]
        if len(names) != 1 or hashlib.sha256(archive.read(names[0])).hexdigest() != sha(init):
            raise ValueError("PS initialization is not from the same XSA")
    identity = {"build_manifest": str(build_path), "build_manifest_sha256": sha(build_path),
                "binaries": binaries, "artifact_counts_verified": counts,
                "contract_sha256": CONTRACT_HASH, "pcm_sha256": PCM_HASH,
                "expected_mfcc_sha256": hashlib.sha256(expected["c_fixed_full_dev_mfcc"]).hexdigest(),
                "expected_metadata_sha256": hashlib.sha256(meta.tobytes()).hexdigest(),
                "ps7_init": str(init), "ps7_init_sha256": sha(init), "xsa": str(xsa), "xsa_sha256": sha(xsa),
                "expected_clocks": expected_configuration(xsa, init),
                "compiler": build["compiler"], "flags": build["flags"], "core_extra_flags": build["core_extra_flags"],
                "timing_scope": build["timing_scope"], "algorithm_accuracy": "NOT_ACCEPTED", "reset_policy": RESET_POLICY,
                "tools": {str(p): sha(p) for p in (nm, gdb)},
                "dependencies": {str(PROJECT / "verification/arm" / n): sha(PROJECT / "verification/arm" / n)
                                 for n in ("jtag.py", "clocks.py", "uart.py")}}
    dump(out / "identity.json", identity)
    np.save(out / "expected_raw13_q24.npy", stage("mfcc_q24"), allow_pickle=False)
    np.save(out / "expected_metadata.npy", meta, allow_pickle=False)
    return identity, dev, stage("mfcc_q24"), meta


def script(args, identity, mode, dest):
    b = identity["binaries"][mode]
    syms = b["symbols"]
    def address(name):
        return syms[name]["address"]
    def save(name, filename):
        s = syms[name]
        return memory_file(s["address"], s["bytes"], dest / filename)
    text = 'set active_bp ""\nset target_selected 0\nset failure [catch {\n'
    text += selection(args.url, args.target_filter or 'name == "Cortex-A9 #0" && jtag_cable_serial == "OBSERVED_SERIAL"',
                      args.cable_serial or "OBSERVED_SERIAL")
    text += 'set target_selected 1\n'
    text += reset_initialization(dict(url=args.url,
        target_filter=args.target_filter or 'name == "Cortex-A9 #0" && jtag_cable_serial == "OBSERVED_SERIAL"',
        cable_serial=args.cable_serial or "OBSERVED_SERIAL", ps7_init=identity["ps7_init"]))
    text += f'dow {word(b["path"])}\n'
    if mode == "validate":
        text += f'set active_bp [bpadd -addr 0x{address("arm_cf_full_ready"):x} -type hw]\n'
        text += f'con -block -timeout {args.timeout}\n'
        text += 'set observed_pc "0x[dict get [rrd -nvlist pc] pc]"\n'
        text += f'if {{$observed_pc != {address("arm_cf_full_ready")}}} {{error "Did not stop at fixed ready breakpoint"}}\n'
        text += 'bpremove $active_bp\nset active_bp ""\n'
        # The firmware flushes these controls before READY and invalidates afterwards.
        text += f'mwr 0x{address("arm_c_fixed_full_trace_frame"):x} {args.trace_frame}\n'
        text += f'mwr 0x{address("arm_c_fixed_full_dump_all_pre"):x} 0\n'
        text += save("c_fixed_full_dev_pcm", "pcm_readback.bin")
        text += save("c_fixed_full_dev_mfcc", "expected_raw13_readback.bin")
        text += save("c_fixed_full_dev_metadata", "expected_metadata_readback.bin")
    text += f'set active_bp [bpadd -addr 0x{address("arm_cf_full_finished"):x} -type hw]\n'
    text += f'con -block -timeout {args.timeout}\n'
    text += 'set observed_pc "0x[dict get [rrd -nvlist pc] pc]"\n'
    text += f'if {{$observed_pc != {address("arm_cf_full_finished")}}} {{error "Did not stop at fixed finished breakpoint"}}\n'
    text += 'bpremove $active_bp\nset active_bp ""\n'
    for symbol, filename in (("arm_c_fixed_full_status", "status.bin"), ("arm_c_fixed_full_results", "results.bin"),
                             ("arm_c_fixed_full_state", "state.bin")):
        text += save(symbol, filename)
    if mode == "validate":
        text += save("arm_c_fixed_full_preemphasis", "preemphasis.bin")
        text += save("arm_c_fixed_full_trace", "trace.bin")
    else:
        text += save("arm_c_fixed_full_ticks", "ticks.bin")
    text += 'puts "CPU_REGISTERS [rrd -nvlist]"\n'
    text += 'puts "RUNTIME_SCTLR [rrd -nvlist cp15 c1 sctlr]"\n'
    text += 'puts "RUNTIME_CPSR [rrd -nvlist cpsr]"\n'
    text += 'puts "RUNTIME_FPSCR [rrd -nvlist vfp fpscr]"\n'
    text += 'puts "FIXED_BOARD_READBACK_COMPLETE"\n} message options]\n'
    text += 'if {$failure} {\nputs stderr "FIXED_BOARD_FAILED $message"\n'
    text += 'catch {puts stderr "FAILURE_TARGETS [targets -target-properties]"}\nif {$target_selected} {\ncatch {stop}\ncatch {puts stderr "FAILURE_REGISTERS [rrd -nvlist]"}\n'
    text += f'catch {{{save("arm_c_fixed_full_status", "failure_status.bin").strip()}}}\n'
    text += 'if {$active_bp ne ""} {catch {bpremove $active_bp}}\n}\nexit 1\n}\nexit\n'
    return text


def scalar(data, layout, field, signed=False):
    entry = layout["fields"][field]
    return int.from_bytes(data[entry["offset"]:entry["offset"] + entry["bytes"]], "little", signed=signed)


def checksum(raw, meta):
    total = MAGIC
    for r, m in zip(raw, meta):
        for value in list(map(int, r)) + [int(v) & 0xffffffff for v in m]:
            value &= (1 << 64) - 1
            total = (((((total << 5) | (total >> 27)) & 0xffffffff) ^ (value & 0xffffffff))
                     + (value >> 32) + 0x9e3779b9) & 0xffffffff
    return total


def compare(identity, mode, dest, expected_raw, expected_meta, contract, dev, trace_frame, *, actual_board_executed=True):
    b = identity["binaries"][mode]
    status_bytes = (dest / "status.bin").read_bytes()
    sl = b["layouts"]["arm_cf_full_status"]
    if len(status_bytes) != sl["bytes"]:
        raise ValueError("Truncated status")
    status = {name: scalar(status_bytes, sl, name) for name in sl["fields"] if name != "arm_clock_registers"}
    clockfield = sl["fields"]["arm_clock_registers"]
    clockdata = status_bytes[clockfield["offset"]:clockfield["offset"] + clockfield["bytes"]]
    status["arm_clock_registers"] = list(struct.unpack("<4I", clockdata))
    dump(dest / "status.json", status)
    rl = b["layouts"]["arm_cf_full_record"]
    rb = (dest / "results.bin").read_bytes()
    if len(rb) != rl["bytes"] * FRAMES:
        raise ValueError("Truncated results")
    raw = np.ndarray((FRAMES, 13), dtype="<i8", buffer=rb, offset=rl["fields"]["raw13"]["offset"], strides=(rl["bytes"], 8)).copy()
    meta = np.ndarray((FRAMES, 8), dtype="<i4", buffer=rb, offset=rl["fields"]["metadata"]["offset"], strides=(rl["bytes"], 4)).copy()
    np.save(dest / "raw13_q24.npy", raw, allow_pickle=False)
    np.save(dest / "metadata.npy", meta, allow_pickle=False)
    np.savetxt(dest / "raw13_q24.csv", raw, fmt="%d", delimiter=",")
    np.savetxt(dest / "metadata.csv", meta, fmt="%d", delimiter=",", header="frame_id,start_sample,s,power_exp,mel_exp,fft_overflow,input_clips,bfp_clamped", comments="")
    result = {"actual_board_executed": actual_board_executed, "passed": False, "frames": FRAMES, "mfcc_values": int(raw.size),
              "raw13_mismatches": int(np.count_nonzero(raw != expected_raw)),
              "metadata_mismatches": int(np.count_nonzero(meta != expected_meta)),
              "status": status, "algorithm_accuracy": "NOT_ACCEPTED", "output_fractional_bits": 24}
    dump(dest / "comparison.json", result)
    checks = {"magic": MAGIC, "mode": 1 if mode == "validate" else 2,
              "completed": 1 if mode == "validate" else 30, "error": 0, "mismatches": 0,
              "frames": FRAMES, "samples": SAMPLES, "checksum": checksum(raw, meta)}
    for k, v in checks.items():
        if status[k] != v:
            raise ValueError(f"Status {k}: {status[k]} != {v}")
    if result["raw13_mismatches"] or result["metadata_mismatches"] or np.any(raw < -(1 << 39)) or np.any(raw >= 1 << 39):
        raise ValueError("Raw signed40/F24 output or metadata differs from frozen model")
    state_bytes = (dest / "state.bin").read_bytes()
    state_layout = b["layouts"]["cf_full_state"]
    state = {name: scalar(state_bytes, state_layout, name, signed=name == "previous_pcm")
             for name in state_layout["fields"] if name != "pre_ring"}
    dump(dest / "state.json", state)
    if len(state_bytes) != state_layout["bytes"] or state["samples_seen"] != SAMPLES or state["frames_emitted"] != FRAMES or state["sticky_status"] != 0 or state["finished"] != 1:
        raise ValueError("Unexpected final streaming state")
    last_pcm = np.fromfile(contract / dev["pcm"]["file"], dtype="<i2")[-1]
    if state["previous_pcm"] != last_pcm:
        raise ValueError("Final previous PCM differs; incomplete tail was not preserved")
    result["clocks"] = verify_configuration(clockdata, identity["expected_clocks"],
        {"cpu_hz": status["cpu_hz_nominal"], "timer_hz": status["timer_hz_nominal"]})
    if status["sctlr"] & ((1 << 12) | (1 << 2)) != ((1 << 12) | (1 << 2)) or not status["l2_cache_control"] & 1:
        raise ValueError("Expected instruction/data/L2 caches enabled")
    if status["timer_control"] & 0xff01 != 1:
        raise ValueError("Unexpected global timer enable/prescaler")
    if mode == "validate":
        if sha(dest / "pcm_readback.bin") != PCM_HASH or sha(dest / "expected_raw13_readback.bin") != identity["expected_mfcc_sha256"] or sha(dest / "expected_metadata_readback.bin") != identity["expected_metadata_sha256"]:
            raise ValueError("Target embedded input/expectation readback differs")
        pre = np.fromfile(dest / "preemphasis.bin", dtype="<i4")
        expected_pre = np.fromfile(contract / dev["stages"]["preemphasis_q30"]["file"], dtype="<i4")
        if pre.shape != expected_pre.shape or not np.array_equal(pre, expected_pre):
            raise ValueError("Full pre-emphasis differs from published contract")
        result["preemphasis_mismatches"] = 0
        trace = (dest / "trace.bin").read_bytes()
        fl, spectral = b["layouts"]["cf_full_output"], b["layouts"]["cf_result"]
        trace_checks = {}
        for field, stage_name, dtype, count in (("windowed_q30", "windowed_q30", "<i4", 512), ("fft_input", "fft_input", "<i2", 512),
                ("log_q24", "log_q24", "<i4", 26), ("floor", "floor", "<u4", 26), ("mfcc_q24", "mfcc_q24", "<i8", 13),
                ("fft_re", "fft_re", "<i4", 512), ("fft_im", "fft_im", "<i4", 512),
                ("power", "power_u40", "<u8", 257), ("mel", "mel_u60", "<u8", 26)):
            offset = fl["fields"][field]["offset"] if field in fl["fields"] else fl["fields"]["spectral"]["offset"] + spectral["fields"][field]["offset"]
            actual = np.frombuffer(trace, dtype=dtype, count=count, offset=offset)
            entry = dev["stages"][stage_name]
            expected = np.fromfile(contract / entry["file"], dtype=entry["dtype"]).reshape(entry["shape"])[trace_frame]
            trace_checks[field] = int(np.count_nonzero(actual != expected))
        result["selected_trace_frame"] = trace_frame
        result["selected_trace_mismatches"] = trace_checks
        if any(trace_checks.values()) or scalar(trace, fl, "frame_id") != trace_frame:
            raise ValueError("Selected intermediate trace differs")
    else:
        ticks = np.fromfile(dest / "ticks.bin", dtype="<u8").tolist()
        if status["warmups"] != 3 or status["repetitions"] != 30 or len(ticks) != 30 or min(ticks) <= 0:
            raise ValueError("Incomplete timing repetitions")
        hz = status["timer_hz_nominal"]
        result["timing"] = {"ticks": ticks, "timer_hz": hz, "warmups": 3, "repeats": 30,
            "scope": identity["timing_scope"], "read_pair_ticks_not_subtracted": status["timer_read_pair_ticks"],
            "clip_ms": {"min": min(ticks)*1000/hz, "median": statistics.median(ticks)*1000/hz,
                        "mean": statistics.mean(ticks)*1000/hz, "max": max(ticks)*1000/hz,
                        "p95": float(np.percentile(ticks, 95))*1000/hz,
                        "stdev": statistics.stdev(ticks)*1000/hz},
            "median_per_frame_ms": statistics.median(ticks)*1000/hz/FRAMES}
    result["passed"] = True
    dump(dest / "comparison.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    a = p.add_mutually_exclusive_group()
    a.add_argument("--execute", action="store_true")
    a.add_argument("--prepare-only", action="store_true")
    p.add_argument("--build-manifest", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--target-filter")
    p.add_argument("--cable-serial")
    p.add_argument("--acknowledge-board", choices=["ZYBO_Z7_20"])
    p.add_argument("--xsct", type=Path, default=Path("C:/Xilinx/Vitis/2024.2/bin/xsct.bat"))
    p.add_argument("--url", default="TCP:127.0.0.1:3121")
    p.add_argument("--uart-port")
    p.add_argument("--initialize-ps", action="store_true", help="Compatibility option; system reset and PS init always run before every ELF")
    p.add_argument("--skip-timing", action="store_true")
    p.add_argument("--trace-frame", type=int, default=0)
    p.add_argument("--timeout", type=int, default=180)
    args = p.parse_args()
    out = args.output_dir.resolve()
    if not out.is_relative_to((PROJECT.parent / "build/board_validation").resolve()) or out.exists():
        p.error("output-dir must be a new directory below build/board_validation")
    if args.timeout < 1 or args.timeout > 3600 or not 0 <= args.trace_frame < FRAMES:
        p.error("timeout must be 1..3600 seconds and trace-frame 0..533")
    if args.execute and not all((args.target_filter, args.cable_serial, args.xsct.is_file(), args.acknowledge_board)):
        p.error("Execution requires observed target-filter, cable-serial, installed xsct and --acknowledge-board ZYBO_Z7_20")
    out.mkdir(parents=True)
    manifest = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "status": "preparing",
                "board_executed": False, "timing_measured": False, "algorithm_accuracy": "NOT_ACCEPTED",
                "argv": sys.argv, "runner_sha256": sha(Path(__file__))}
    capture = None
    try:
        for source in [Path(__file__), PROJECT / "verification/arm/test_board_fixed.py"] + [
                PROJECT / "verification/arm" / name for name in ("jtag.py", "clocks.py", "uart.py")]:
            target = out / "source" / source.relative_to(PROJECT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        identity, dev, raw, meta = prepare(args, out)
        for mode in ("validate", "timing"):
            (out / mode / "run.tcl").write_text(script(args, identity, mode, out / mode), encoding="utf-8")
        manifest["preparation_passed"] = True
        manifest["identity_sha256"] = sha(out / "identity.json")
        manifest["hardware_selection"] = {"url": args.url, "target_filter": args.target_filter,
            "cable_serial": args.cable_serial, "uart_port": args.uart_port, "initialize_ps": True,
            "initialize_ps_flag_requested": args.initialize_ps, "reset_policy": RESET_POLICY,
            "acknowledge_board": args.acknowledge_board}
        if not args.execute:
            manifest["status"] = "prepared_not_run"
            return 0
        manifest["xsct"] = {"path": str(args.xsct), "sha256": sha(args.xsct)}
        if args.uart_port:
            capture = Capture(args.uart_port, out)
            capture.start()
        for mode in ("validate", "timing"):
            if mode == "timing" and args.skip_timing:
                break
            if mode == "timing" and not manifest.get("validation_passed"):
                raise ValueError("Timing requires actual same-run host bitwise validation")
            if sha(args.build_manifest) != identity["build_manifest_sha256"]:
                raise ValueError("Build manifest changed during board execution")
            for entry in identity["binaries"].values():
                if sha(entry["path"]) != entry["sha256"]:
                    raise ValueError("ELF changed during board execution")
            dest = out / mode
            manifest["execution_attempted"] = True
            dump(out / "run_manifest.json", manifest)
            xsct_output = process([args.xsct, dest / "run.tcl"], dest / "xsct.log", timeout=args.timeout * 2 + 90)
            if "FIXED_BOARD_READBACK_COMPLETE" not in xsct_output:
                raise ValueError("XSCT returned without successful fixed readback sentinel")
            manifest["board_executed"] = True
            result = compare(identity, mode, dest, raw, meta, args.contract, dev, args.trace_frame)
            if mode == "validate":
                manifest["validation_passed"] = result["passed"]
            else:
                manifest["timing_measured"] = True
                dump(out / "timing_summary.json", result["timing"])
        manifest["status"] = "passed"
        return 0
    except Exception as error:
        manifest.update(status="failed", error=str(error), traceback=traceback.format_exc())
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if capture:
            capture.close()
        manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        dump(out / "run_manifest.json", manifest)
        dump(out / "artifact_manifest.json", {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*"))
                                             if p.is_file() and p.name != "artifact_manifest.json"})
        print(json.dumps({k: manifest.get(k) for k in ("status", "board_executed", "validation_passed", "timing_measured", "error")}))


if __name__ == "__main__":
    raise SystemExit(main())
