"""Snapshot and verify the W20 FFT-output -> W40 Power -> W60 Mel RTL."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from software.fixed_model.coeffs import quantize_mel_filterbank
from software.fixed_model.power_mel import power_integer, mel_accumulate

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

def read_timing_report(path):
    """Separate route completion from setup/hold/pulse timing success."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    index = next(i for i,line in enumerate(lines) if line.strip().startswith("WNS(ns)"))
    values = lines[index+2].split()
    timing = {"wns_ns":float(values[0]), "tns_ns":float(values[1]),
              "whs_ns":float(values[4]), "ths_ns":float(values[5]),
              "wpws_ns":float(values[8]), "tpws_ns":float(values[9])}
    timing["met"] = min(timing["wns_ns"],timing["whs_ns"],timing["wpws_ns"]) >= 0
    report = "\n".join(lines)
    for label in ("no_clock","unconstrained_internal_endpoints","no_input_delay","no_output_delay","latch_loops"):
        match = re.search(r"checking " + label + r" \((\d+)\)", report)
        timing[label] = int(match.group(1)) if match else None
    timing["scope"] = "OOC internal clock paths; external I/O paths require integrated constraints"
    return timing

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", required=True)
    p.add_argument("--limit", type=int)
    p.add_argument("--implementation", choices=("none", "synth", "route"), default="route")
    p.add_argument("--baseline", type=Path, default=PROJECT.parent / "build/fft_precision/prec_04_verified_full_20261004")
    args = p.parse_args()
    if not args.run_id.replace("_", "").replace("-", "").isalnum(): raise ValueError("simple fresh run-id required")
    out = PROJECT.parent / "build/fixed_power_mel" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"status":"preparing", "scope":"FFT-output integer Power/Mel, no FFT arithmetic in this unit", "baseline":str(args.baseline), "evaluation_audio_used":False, "board_run":False, "source_hashes":{}, "input_hashes":{}}
    table = quantize_mel_filterbank(16)
    coeffs = [x for row in table.weights_int for x in row]
    rom = "".join(f"{v:05x}\n" for v in coeffs+[0]*(8192-len(coeffs)))
    (out / "mel_fw16.mem").write_text(rom, encoding="ascii")
    canonical = PROJECT / "hardware/fixed/power_mel/mel_fw16.mem"
    if not canonical.exists(): canonical.write_text(rom, encoding="ascii")
    if canonical.read_text(encoding="ascii") != rom: raise ValueError("canonical coefficients changed")
    manifest["coefficient_manifest"] = table.manifest()
    vectors=[]
    for re_path in sorted((args.baseline / "arrays").glob("*t0p975_d20_out20_in16_fft_re.bin")):
        base = str(re_path)[:-len("fft_re.bin")]
        paths = [Path(base+name) for name in ("fft_re.bin","fft_im.bin","shift.bin","psum.bin","mel.bin")]
        for path in paths: manifest["input_hashes"][str(path)] = sha(path)
        real = np.fromfile(paths[0], dtype="<i8").reshape(-1,257)
        imag = np.fromfile(paths[1], dtype="<i8").reshape(-1,257)
        shifts = np.fromfile(paths[2], dtype="<i8")
        powers = np.fromfile(paths[3], dtype="<i8").reshape(-1,257)
        mels = np.fromfile(paths[4], dtype="<i8").reshape(-1,26)
        for f,(r,i,s) in enumerate(zip(real,imag,shifts)):
            ps=power_integer(r,i,in_width=20,psum_width=40)
            me=mel_accumulate(ps,table,accumulator_width=60)
            if ps != powers[f].tolist() or me != mels[f].tolist(): raise AssertionError("baseline arithmetic changed")
            vectors.append((r.tolist()+[0]*255,i.tolist()+[0]*255,int(s),0,ps,me))
    manifest["baseline_frames_available"] = len(vectors)
    if len(vectors) != 616: raise AssertionError(f"expected616frames got{len(vectors)}")
    if args.limit is not None: vectors=vectors[:args.limit]
    manifest["baseline_frames_executed"] = len(vectors)
    # Full signed20 boundaries prove W40/W60 sufficient. Overflow input propagation
    # is injected once, then an ordinary frame verifies per-frame recovery.
    for value,sv,err in ((-524288,-2,0),(524287,24,0),(-1,0,0),(0,0,1),(11,7,0)):
        r=[value]*512; i=[value]*512
        ps=power_integer(r,i,num_bins=257,in_width=20,psum_width=40)
        me=mel_accumulate(ps,table,accumulator_width=60)
        vectors.append((r,i,sv,err,ps,me))
    streams={name:(out/name).open("w",encoding="ascii") for name in ("fft.mem","power.mem","mel_expected.mem","shift.mem","overflow.mem")}
    for r,i,s,e,ps,me in vectors:
        streams["fft.mem"].writelines(f"{((a&0xfffff)<<20)|(b&0xfffff):010x}\n" for a,b in zip(r,i))
        streams["power.mem"].writelines(f"{v:010x}\n" for v in ps)
        streams["mel_expected.mem"].writelines(f"{v:015x}\n" for v in me)
        streams["shift.mem"].write(f"{s&255:02x}\n")
        streams["overflow.mem"].write(f"{e:x}\n")
    for stream in streams.values(): stream.close()
    sources = [Path(__file__), PROJECT/"scripts/fixed_power_mel.tcl", PROJECT/"hardware/fixed/power_mel/fixed_spectral_tail.sv", canonical,
               PROJECT/"hardware/fixed/power_mel/fixed_mel_descriptor.sv", PROJECT/"hardware/fixed/power_mel/mel_sparse_fw16.mem", PROJECT/"scripts/generate_sparse_mel.py",
               PROJECT/"hardware/fixed/power_mel/README.md", PROJECT/"verification/fixed/power_mel/tb_fixed_spectral_tail.sv",
               PROJECT/"software/fixed_model/coeffs.py", PROJECT/"software/fixed_model/power_mel.py", PROJECT/"software/fixed_model/qnum.py"]
    for src in sources:
        rel=src.relative_to(PROJECT); dst=out/"source"/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dst)
        manifest["source_hashes"][rel.as_posix()] = sha(dst)
    (out/"clock.xdc").write_text("create_clock -period 10.000 -name clk [get_ports clk]\n",encoding="ascii")
    command=["C:/Xilinx/Vivado/2024.2/bin/vivado.bat","-mode","batch","-source",str(out/"source/scripts/fixed_power_mel.tcl"),"-tclargs",str(out),str(len(vectors)),args.implementation]
    manifest.update(status="running",command=command,frames=len(vectors),started_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    dump(out/"run_manifest.json",manifest)
    try:
        with (out/"console.log").open("w",encoding="utf-8") as log:
            result=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
        manifest["vivado_exit_code"]=result.returncode
        if result.returncode: raise RuntimeError("Vivado failed; inspect console.log")
        manifest["protocol"]=json.loads((out/"protocol.json").read_text())
        if manifest["protocol"]["frames"] != len(vectors): raise AssertionError("count")
        if args.implementation != "none":
            cells=(out/"bram_cells.txt").read_text().splitlines()
            if not cells: raise AssertionError("no actual BRAM mapping")
            manifest["actual_bram_cells"]=cells
            manifest["synthesis_timing"] = read_timing_report(out/"timing_synth.rpt")
        if args.implementation == "route":
            manifest["routed_timing"] = read_timing_report(out/"timing_route.rpt")
        manifest.update(status="passed",simulation="passed",synthesis=args.implementation!="none",place_route=args.implementation=="route")
        if args.implementation == "route" and not manifest["routed_timing"]["met"]:
            manifest["status"] = "bit_match_passed_timing_failed"
    except Exception as error:
        manifest.update(status="failed",error=str(error)); raise
    finally:
        manifest["completed_at_utc"]=dt.datetime.now(dt.timezone.utc).isoformat()
        dump(out/"run_manifest.json",manifest)
        dump(out/"artifact_hashes.json",{str(f.relative_to(out)):sha(f) for f in out.rglob("*") if f.is_file() and f.name!="artifact_hashes.json"})
        print(json.dumps({"run":str(out),"status":manifest["status"]}))

if __name__=="__main__": main()
