"""Offline contract/decoder and mocked Tcl guards; never opens hardware or UART."""
import argparse
from argparse import Namespace
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fixed_board", PROJECT / "scripts/run_board_fixed.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    identity = runner.read(args.preparation / "identity.json")
    raw = np.load(args.preparation / "expected_raw13_q24.npy")
    meta = np.load(args.preparation / "expected_metadata.npy")
    dev = next(c for c in runner.read(args.contract / "contract.json")["cases"] if c["group"] == "development")
    results = {"actual_board_executed": False, "fixture_kind": "synthetic host-generated readback for parser tests", "checks": []}
    def stage(name):
        e = dev["stages"][name]
        return np.fromfile(args.contract / e["file"], dtype=e["dtype"]).reshape(e["shape"])
    def fixture(mode):
        dest = args.output / mode
        dest.mkdir()
        b = identity["binaries"][mode]
        state = bytearray(2080)
        struct.pack_into("<h", state, 2048, int(np.fromfile(args.contract / dev["pcm"]["file"], dtype="<i2")[-1]))
        struct.pack_into("<7I", state, 2052, 85920, 534, 416, 32, 0, 1, 1)
        (dest / "state.bin").write_bytes(state)
        (dest / "results.bin").write_bytes(b"".join(struct.pack("<13q8i", *r, *m) for r, m in zip(raw, meta)))
        clocks = [identity["expected_clocks"]["registers"][k]["value"] for k in ("ARM_PLL_CTRL", "DDR_PLL_CTRL", "IO_PLL_CTRL", "ARM_CLK_CTRL")]
        values = [runner.MAGIC, 1 if mode == "validate" else 2, 1 if mode == "validate" else 30, 0, 0,
                  runner.checksum(raw, meta), 534, 85920, 666666687, 333333343, 0x1004, 0x13, 1, 1,
                  *clocks, 2080, 9216, 9784, 16, 30824, 3400, 0 if mode == "validate" else 3, 0 if mode == "validate" else 30]
        (dest / "status.bin").write_bytes(struct.pack("<26IQ", *values, 24))
        if mode == "validate":
            (dest / "pcm_readback.bin").write_bytes((args.contract / dev["pcm"]["file"]).read_bytes())
            (dest / "expected_raw13_readback.bin").write_bytes(raw.astype("<i8").tobytes())
            (dest / "expected_metadata_readback.bin").write_bytes(meta.astype("<i4").tobytes())
            (dest / "preemphasis.bin").write_bytes(stage("preemphasis_q30").astype("<i4").tobytes())
            trace = bytearray(9784)
            for off, name, dtype in ((0,"fft_re","<i4"),(2048,"fft_im","<i4"),(4096,"power_u40","<u8"),
                    (6152,"mel_u60","<u8"),(6384,"windowed_q30","<i4"),(8432,"fft_input","<i2"),
                    (9456,"log_q24","<i4"),(9560,"floor","<u4"),(9664,"mfcc_q24","<i8")):
                data = stage(name)[0].astype(dtype).tobytes()
                trace[off:off+len(data)] = data
            (dest / "trace.bin").write_bytes(trace)
        else:
            (dest / "ticks.bin").write_bytes(struct.pack("<30Q", *range(1000000, 1000030)))
        return dest
    def compare(mode, dest):
        return runner.compare(identity, mode, dest, raw, meta, args.contract, dev, 0, actual_board_executed=False)
    for mode in ("validate", "timing"):
        dest = fixture(mode)
        assert compare(mode, dest)["passed"]
        results["checks"].append(mode + "_synthetic_readback_passes")
    # Preserve the valid fixture; each corruption is a distinct test directory.
    import shutil
    for name, filename, offset in (("coefficient", "results.bin", 0), ("metadata", "results.bin", 104),
                                   ("status", "status.bin", 12), ("preemphasis", "preemphasis.bin", 0),
                                   ("trace", "trace.bin", 6384), ("input", "pcm_readback.bin", 0)):
        dest = args.output / ("reject_" + name)
        shutil.copytree(args.output / "validate", dest)
        payload = bytearray((dest / filename).read_bytes())
        payload[offset] ^= 1
        (dest / filename).write_bytes(payload)
        try:
            compare("validate", dest)
        except ValueError as error:
            results["checks"].append("reject_" + name)
            (dest / "expected_rejection.txt").write_text(str(error), encoding="utf-8")
        else:
            raise AssertionError("Corrupt readback accepted: " + name)
    tclsh = Path("C:/Xilinx/Vitis/2024.2/tps/win64/git-2.45.0/mingw64/bin/tclsh.exe")
    for label, ambiguous, stop_error, wrong_pc in (("ok", 0, "", 0), ("already_stopped", 0, "Already stopped", 0),
            ("ambiguous", 1, "", 0), ("stop_error", 0, "Transport lost", 0), ("wrong_pc", 0, "", 1)):
        b = identity["binaries"]["validate"]
        dest = args.output / ("tcl_" + label)
        dest.mkdir()
        scriptargs = Namespace(url="OFFLINE_MOCK", target_filter='name == "Cortex-A9 #0"', cable_serial="MOCK_ONLY",
                               initialize_ps=False, timeout=1, trace_frame=0)
        preamble = f'''set stops 0
set current_pc 0
set selected 0
proc connect {{args}} {{puts "MOCK_CONNECT"}}
proc targets {{args}} {{
  if {{[lindex $args 0] eq "-set"}} {{set ::selected [lindex $args 1]; puts "MOCK_SELECT $::selected"; return}}
  set one [dict create name {{Cortex-A9 #0}} jtag_cable_serial MOCK_ONLY target_id 2]
  set two [dict create name {{Cortex-A9 #1}} jtag_cable_serial MOCK_ONLY target_id 3]
  if {{[llength $args] == 1}} {{return [list $one $two]}}
  if {{{ambiguous}}} {{return [list $one $one]}}
  return [list $one]
}}
proc stop {{args}} {{incr ::stops; puts "MOCK_STOP $::selected"; if {{{json.dumps(stop_error)}}} {{error {json.dumps(stop_error)}}}}}
proc rst {{args}} {{puts "MOCK_RESET $args"}}
proc source {{args}} {{}}
proc ps7_init {{args}} {{puts "MOCK_PS_INIT"}}
proc ps7_post_config {{args}} {{puts "MOCK_PS_POST"}}
proc dow {{args}} {{puts "MOCK_DOWNLOAD"}}
proc mwr {{args}} {{}}
proc mrd {{args}} {{return 0}}
proc bpadd {{args}} {{set ::current_pc [lindex $args 1]; return 1}}
proc bpremove {{args}} {{}}
proc con {{args}} {{}}
proc rrd {{args}} {{
 if {{[lindex $args end] eq "pc"}} {{return [dict create pc [format %08x [expr {{$::current_pc + {wrong_pc}}}]]]}}
 return [dict create fpscr 00000000]
}}
'''
        # Tcl braces around a quoted empty string need explicit string comparison.
        preamble = preamble.replace('if {""}', 'if {"" ne ""}').replace('if {"Already stopped"}', 'if {"Already stopped" ne ""}').replace('if {"Transport lost"}', 'if {"Transport lost" ne ""}')
        script = dest / "mock.tcl"
        script.write_text(preamble + runner.script(scriptargs, identity, "validate", dest), encoding="utf-8")
        proc = subprocess.run([str(tclsh), str(script)], capture_output=True, text=True, timeout=10)
        (dest / "mock.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
        expected_success = label in ("ok", "already_stopped")
        assert (proc.returncode == 0 and "FIXED_BOARD_READBACK_COMPLETE" in proc.stdout) == expected_success, (label, proc.stdout, proc.stderr)
        if not expected_success:
            assert proc.returncode == 1
        else:
            assert proc.stdout.count("MOCK_CONNECT") == 1
            markers = ["MOCK_RESET -system -stop", "MOCK_STOP 3", "MOCK_STOP 2", "MOCK_PS_INIT", "MOCK_PS_POST", "MOCK_DOWNLOAD"]
            positions = [proc.stdout.index(marker) for marker in markers]
            assert positions == sorted(positions), (label, proc.stdout)
        results["checks"].append("tcl_" + label)
    results["checks"].append("one_connection_and_system_reset_cpu1_halt_ps_init_before_download_sequence")
    results["passed"] = True
    runner.dump(args.output / "report.json", results)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
