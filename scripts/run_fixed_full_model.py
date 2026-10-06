#!/usr/bin/env python3
"""Generate offline coefficients; verify PCM->MFCC integer runtime on all 24 cases."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_fft_precision_study import collect_cases, load_checked_arrays, err, sha256_file
from scripts.run_fixed_pilot import DEFAULT_REFERENCE, PROFILE
from software.fixed_model.coeffs import quantize_mel_filterbank
from software.fixed_model.fft_bitmodel import load_twiddle_rom
from software.fixed_model.full_integer import run_pcm, log_integer, LOG_FLOOR_Q24


def generate_coefficients(reference=DEFAULT_REFERENCE):
    arrays, hashes = load_checked_arrays(reference / "coefficients" / PROFILE)
    coefficients = {"schema_version": 2, "window_q30": np.rint(arrays["window"] * (1 << 30)).astype(np.int64).tolist(),
                    "dct_q30": np.rint(arrays["dct_matrix"] * (1 << 30)).astype(np.int64).tolist(),
                    "mel_q16": quantize_mel_filterbank(16).weights_int,
                    "ln2_q30": 744261118, "log_floor_q24": LOG_FLOOR_Q24,
                    "source_hashes": hashes}
    return coefficients, arrays


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-root", type=Path, default=Path(r"D:\2610_MFCC\build\fixed_full_model"))
    parser.add_argument("--reference-run", type=Path, default=DEFAULT_REFERENCE)
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id:
        raise ValueError("run-id must be a single folder name")
    out = args.out_root / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    (out / "arrays").mkdir()
    (out / "source_snapshot").mkdir()
    start = time.perf_counter()
    coefficients, frozen = generate_coefficients(args.reference_run)
    (out / "coefficients.json").write_text(json.dumps(coefficients, indent=2), encoding="utf-8")
    cases, checks, counts = collect_cases(args.reference_run, {"dev", "synthetic", "extra"}, out, None, frozen)
    twiddle = load_twiddle_rom(str(ROOT / "software/fixed_model/coefficients/twiddle_1024_w16.mem"))
    reports, groups = [], {}
    for case in cases:
        cid, ref = case["id"], case["arrays"]
        pcm = np.fromfile(out / "arrays" / case["pcm"]["file"], dtype="<i2")
        result = run_pcm(pcm, coefficients, twiddle)
        integer_arrays = {k: np.asarray(v, dtype=np.int64) for k, v in result.items()}
        for key, cols in (("windowed_q30",512),("fft_input",512),("fft_re",512),("fft_im",512),("power_u40",257),("mel_u60",26),("log_q24",26),("floor",26),("mfcc_q24",13)):
            integer_arrays[key] = integer_arrays[key].reshape(-1, cols)
        path = out / "arrays" / (cid + "_integer.npz")
        np.savez_compressed(path, **integer_arrays)
        logs = integer_arrays["log_q24"] / 2**24
        mfcc = integer_arrays["mfcc_q24"] / 2**24
        mel = np.ldexp(integer_arrays["mel_u60"].astype(np.float64), integer_arrays["mel_exponent"][:, None])
        ideal_logs = np.log(np.maximum(mel, 1e-12))
        ideal_mfcc = ideal_logs @ frozen["dct_matrix"].T
        stage_errors = {"windowed": err(ref["windowed"], integer_arrays["windowed_q30"] / 2**30), "mel": err(ref["mel_energies"], mel), "log": err(ref["log_mel"], logs), "mfcc": err(ref["mfcc"], mfcc), "integer_log_only": err(ideal_logs, logs), "integer_log_dct_only": err(ideal_mfcc, mfcc)}
        floor_mismatch = int(np.count_nonzero((mel <= 1e-12) & (ref["mel_energies"] > 1e-12)))
        worst = []
        if mfcc.size:
            for flat in np.argsort(np.abs(mfcc-ref["mfcc"]).ravel())[-3:][::-1]:
                f, c = np.unravel_index(flat, mfcc.shape)
                worst.append({"frame":int(f),"coefficient":int(c),"error":float(mfcc[f,c]-ref["mfcc"][f,c]),"shift_s":result["shift_s"][f],"floored_mels":[i for i,x in enumerate(result["floor"][f]) if x]})
        report = {"id":cid,"group":case["group"],"frames":len(result["shift_s"]),"pcm":case["pcm"],"integer_file":str(path.relative_to(out)),"sha256":sha256_file(path),"errors":stage_errors,"floor_regressions":floor_mismatch,"fft_overflow_frames":sum(result["fft_overflow"]),"input_clips":sum(result["input_clips"]),"worst_mfcc":worst}
        reports.append(report)
        groups.setdefault(case["group"], []).append((ref["mfcc"], mfcc))
        print(json.dumps({"id":cid,"frames":report["frames"],"mfcc":stage_errors["mfcc"],"integer_log_dct_only":stage_errors["integer_log_dct_only"],"floor_regressions":floor_mismatch}), flush=True)
    summary = {g:err(np.concatenate([r for r,c in items]),np.concatenate([c for r,c in items])) for g,items in groups.items()}
    synth = groups["synthetic"] + groups["extra_synthetic"]
    summary["all_synthetic"] = err(np.concatenate([r for r,c in synth]),np.concatenate([c for r,c in synth]))
    hashes = {}
    sources = list((ROOT / "software/fixed_model").glob("*.py")) + [Path(__file__), ROOT / "scripts/run_fixed_pilot.py", ROOT / "scripts/run_fft_precision_study.py", ROOT / "software/fixed_model/coefficients/twiddle_1024_w16.mem"]
    for source in sources:
        rel = source.relative_to(ROOT)
        dest = out / "source_snapshot" / rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,dest)
        hashes[rel.as_posix()] = sha256_file(dest)
    bound = max(sum(abs(x) for x in row) for row in coefficients["dct_q30"]) * abs(LOG_FLOOR_Q24)
    manifest = {"status":"complete","runtime":"integer_only","accuracy_accepted":False,"accuracy_reason":"Thresholds unset; synthetic errors and floor regressions retained","rtl_verified":False,"run_id":args.run_id,"elapsed_seconds":time.perf_counter()-start,"case_counts":counts,"total_frames":sum(r["frames"] for r in reports),"summary":summary,"cases":reports,"source_sha256":hashes,"coefficient_sha256":sha256_file(out / "coefficients.json"),"dct_accumulator_tight_abs_bound":bound,"dct_accumulator_signed_bits":bound.bit_length()+1,"reference_checks":checks}
    (out / "run_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps({"out":str(out),"summary":summary,"frames":manifest["total_frames"],"dct_bound":bound}),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
