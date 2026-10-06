"""Snapshot full FP32 top, import fresh vendor IP, and route OOC; no board I/O."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT.parent

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def inspect_rom_inputs(out):
    """Track every emitted ROM; require only ROMs actually named by frozen RTL."""
    emitted = {path.name: sha(path) for path in sorted((out / "coefficients").glob("*.mem"))}
    referenced = set()
    pattern = (r'(?:\.\w*INIT_FILE\s*\(\s*|'
               r'\b(?:parameter|localparam)\s+(?:string\s+)?\w*INIT_FILE\s*=\s*)'
               r'"([^"\r\n]+\.mem)"')
    for source in sorted((out / "source/hardware/fp32/rtl").glob("*.sv")):
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source.read_text(encoding="utf-8-sig"), flags=re.S)
        referenced.update(re.findall(pattern, code))
    if not referenced:
        raise ValueError("No literal XPM ROM initialization files found in frozen RTL")
    missing = sorted(referenced - emitted.keys())
    if missing:
        raise ValueError("Frozen RTL references absent ROMs: " + ", ".join(missing))
    return emitted, sorted(referenced)


def confirmed_rom_loads(out, referenced):
    log = out / "project/fp32_mfcc.runs/synth_1/runme.log"
    contents = log.read_text(encoding="utf-8", errors="replace")
    return [name for name in referenced if f"$readmem data file '{name}' is read successfully" in contents]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ip-run", type=Path, default=WORK / "build/fp32_hw/ip_04")
    args = parser.parse_args()
    if not re.fullmatch(r"synth_[A-Za-z0-9_-]+", args.run_id):
        parser.error("Use a new synth_ run name")
    out = WORK / "build/fp32_hw" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    (out / "reports").mkdir()
    manifest = dict(started_at_utc=datetime.now(timezone.utc).isoformat(), status="preparing",
        target_part="xc7z020clg400-1", top="fp32_mfcc", clock_period_ns=10.0,
        ooc_clock_source_assumption="BUFGCTRL_X0Y0", external_io_delays_constrained=False,
        physical_board_accessed=False, bitstream_generated=False, source_hashes={},
        numerical_simulation_performed=False, ip_run=str(args.ip_run.resolve()))
    try:
        sources = list((PROJECT / "hardware/fp32/rtl").glob("*.sv"))
        sources += [PROJECT / "hardware/fp32/ip/synth_top.tcl", Path(__file__),
            PROJECT / "verification/fp32/generate_coefficients.py"]
        for source in sources:
            relative = source.relative_to(PROJECT)
            dest = out / "source" / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            manifest["source_hashes"][relative.as_posix()] = sha(dest)
        generator = out / "source/verification/fp32/generate_coefficients.py"
        spec = importlib.util.spec_from_file_location("frozen_coefficients", generator)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.generate(WORK / "build/c_reference/reproduce_01", out / "coefficients")
        manifest["coefficient_manifest_sha256"] = sha(out / "coefficients/coefficient_manifest.json")
        emitted, referenced = inspect_rom_inputs(out)
        manifest["coefficient_file_hashes"] = emitted
        manifest["referenced_rom_init_files"] = referenced
        ipmeta = json.loads((args.ip_run / "ip_manifest.json").read_text(encoding="utf-8-sig"))
        if ipmeta["status"] != "generated_not_synthesized" or ipmeta["target_part"] != "xc7z020clg400-1":
            raise ValueError("Unexpected IP provenance")
        for relative, digest in ipmeta["output_hashes"].items():
            if sha(args.ip_run / relative) != digest:
                raise ValueError("IP artifact changed: " + relative)
        manifest["ip_manifest_sha256"] = sha(args.ip_run / "ip_manifest.json")
        vivado = "C:/Xilinx/Vivado/2024.2/bin/vivado.bat"
        command = [vivado, "-mode", "batch", "-nojournal", "-log", str(out / "vivado.log"),
            "-source", str(out / "source/hardware/fp32/ip/synth_top.tcl"), "-tclargs", str(out), str(args.ip_run.resolve())]
        changed = [name for name, digest in manifest["source_hashes"].items() if sha(PROJECT / name) != digest]
        if changed:
            raise RuntimeError("Source changed during snapshot; use a new stable run: " + ", ".join(changed))
        manifest.update(command=command, status="frozen_before_execution")
        write(out / "freeze.json", manifest)
        write(out / "run_manifest.json", manifest)
        with (out / "console.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
        manifest["vivado_exit_code"] = completed.returncode
        if completed.returncode or not (out / "reports/completion.txt").is_file():
            raise RuntimeError("Vivado did not finish OOC route; inspect console.log")
        manifest["rom_init_files_confirmed"] = confirmed_rom_loads(out, referenced)
        missing_loads = sorted(set(referenced) - set(manifest["rom_init_files_confirmed"]))
        if missing_loads:
            raise RuntimeError("ROM initialization load not confirmed in synthesis log: " + ", ".join(missing_loads))
        manifest["status"] = "routed_ooc_timing_requires_report_review"
    except Exception as error:
        manifest.update(status="failed", error=str(error))
        print(str(error), file=sys.stderr)
    finally:
        manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["source_changed_after_snapshot"] = [name for name, digest in manifest["source_hashes"].items()
            if sha(PROJECT / name) != digest]
        write(out / "run_manifest.json", manifest)
        write(out / "artifact_manifest.json", {p.relative_to(out).as_posix(): sha(p)
            for p in sorted(out.rglob("*")) if p.is_file() and p.name != "artifact_manifest.json" and ".Xil" not in p.parts})
    print(str(out))
    return int(manifest["status"] == "failed")

if __name__ == "__main__":
    raise SystemExit(main())
