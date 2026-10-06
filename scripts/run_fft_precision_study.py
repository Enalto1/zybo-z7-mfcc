#!/usr/bin/env python3
"""FFT precision study: where the error comes from, and what width would help.

Two experiments, both still MIXED PRECISION (float64 pre-emphasis / framing /
window and float64 log / DCT; only the FFT input quantization, the FFT and
Power/Mel are integer).  No RTL is written, simulated or synthesised here.

PART 3 - error source separation.  Same PCM, same frames, same BFP exponent,
same integer Mel weights and the same 1e-12 floor for every path:

  A   float64 reference (frozen arrays)
  A'  Mel coefficient quantization only (float64 power, integer Mel weights)
  B   FFT input quantized, FFT computed in float64, no output rounding
  C   B plus rounding of the FINAL FFT output to the output grid
  D   the full integer FFT bit model (all internal rounding and wrapping)

Errors interact.  The differences between these rows are NOT independent
per-stage contributions and must not be reported as such.

PART 4 - data precision candidates, one factor at a time:

  d16            existing path (input 16 / internal 16 / output 16)
  d20_out16_in16 internal 20 only; input and output stay 16
  d18_out18_in16 internal and output 18; input stays 16
  d20_out20_in16 internal and output 20; input stays 16
  d18_out18_in18 input, internal and output 18
  d20_out20_in20 input, internal and output 20

Only ``d16`` corresponds to existing hardware.  Twiddle precision is held at
the existing 16-bit table for every row, so any difference is due to data
precision alone.

Usage:
  python scripts/run_fft_precision_study.py --run-id prec_01
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from software.fixed_model.coeffs import quantize_mel_filterbank  # noqa: E402
from software.fixed_model.fft_bitmodel import (  # noqa: E402
    fft_fixed_natural, load_twiddle_rom)
from software.fixed_model.fft_bitmodel_wide import (  # noqa: E402
    BASELINE_16BIT, FftWidthConfig, fft_fixed_natural_wide, psum_max_for,
    physical_fft_shift, output_requant_shift, integer_code_shift)
from software.fixed_model.pipeline import (  # noqa: E402
    CandidateConfig, ClipReport, InputQuantConfig, SHIFT_RULE_DEFAULT,
    choose_bfp_shift, quantize_frame_input)
from software.fixed_model.power_mel import (  # noqa: E402
    mel_accumulate, power_exponent, power_integer)
from software.fixed_model.qnum import bits_for_unsigned  # noqa: E402
from scripts.run_fixed_pilot import (  # noqa: E402
    EXTRA_SEED, REQUIRED_ARRAYS, build_extra_inputs,
    float64_frontend, float64_reference_stages)

DEFAULT_REFERENCE = Path(r"D:\2610_MFCC\build\python_reference\reproduce_01")
DEFAULT_OUT_ROOT = Path(r"D:\2610_MFCC\build\fft_precision")
DEV_ID = "8463-294828-0037"
PROFILE = "comparison_raw13"
LOG_FLOOR = 1e-12
NFFT = 512
NUM_BINS = NFFT // 2 + 1
S_TOTAL = 9                      # Physical FFT normalization, NOT F conversion
TARGETS = {"t0p5": 0.5, "t0p975": 0.975}

WIDTH_CONFIGS = {
    "d16": (16, 15, BASELINE_16BIT),
    "d20_out16_in16": (16, 15, FftWidthConfig(
        data_width=20, data_frac=19, output_width=16, output_frac=15,
        label="d20_out16_in16")),
    "d18_out18_in16": (16, 15, FftWidthConfig(
        data_width=18, data_frac=17, label="d18_out18_in16")),
    "d20_out20_in16": (16, 15, FftWidthConfig(
        data_width=20, data_frac=19, label="d20_out20_in16")),
    "d18_out18_in18": (18, 17, FftWidthConfig(
        data_width=18, data_frac=17, label="d18_out18_in18")),
    "d20_out20_in20": (20, 19, FftWidthConfig(
        data_width=20, data_frac=19, label="d20_out20_in20")),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_bin(path: Path, dtype: str, shape) -> np.ndarray:
    a = np.fromfile(path, dtype=dtype)
    want = 1
    for d in shape:
        want *= d
    if a.size != want:
        raise RuntimeError(f"{path}: {a.size} elements, expected {want}")
    return a.reshape(shape)


def err(ref: np.ndarray, got: np.ndarray) -> dict:
    if np.shape(ref) != np.shape(got):
        raise ValueError(f"error statistic shape mismatch: {np.shape(ref)}, {np.shape(got)}")
    if not np.all(np.isfinite(ref)) or not np.all(np.isfinite(got)):
        raise ValueError("non-finite error statistic input")
    d = np.abs(np.asarray(got) - np.asarray(ref))
    return {"max_abs": float(np.max(d)) if d.size else 0.0,
            "rmse": float(np.sqrt(np.mean(d.astype(np.float64) ** 2)))
            if d.size else 0.0}


def save_int_array(path: Path, rows, columns=None) -> dict:
    """Save integer rows, refusing to let anything exceed int64 silently."""
    rows = [list(row) for row in rows]
    if any(not isinstance(v, (int, np.integer)) for row in rows for v in row):
        raise TypeError("integer dump refuses non-integer values")
    flat = [int(v) for row in rows for v in row]
    shape = [len(rows), len(rows[0]) if rows else (columns if columns is not None else 0)]
    if columns is not None and shape[1] != columns:
        raise ValueError("integer dump column count mismatch")
    if any(len(row) != shape[1] for row in rows):
        raise ValueError("ragged integer dump")
    lo = min(flat) if flat else 0
    hi = max(flat) if flat else 0
    i64_min, i64_max = -(1 << 63), (1 << 63) - 1
    if lo < i64_min or hi > i64_max:
        # Store exactly as text rather than truncate.
        with open(path.with_suffix(".txt"), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(" ".join(str(int(v)) for v in row) + "\n")
        return {"file": path.with_suffix(".txt").name, "format": "decimal text",
                "reason": "values exceed int64", "min": lo, "max": hi,
                "shape": shape, "sha256": sha256_file(path.with_suffix(".txt"))}
    np.asarray([[int(v) for v in row] for row in rows],
               dtype="<i8").tofile(path)
    return {"file": path.name, "format": "<i8", "min": lo, "max": hi,
            "shape": shape, "sha256": sha256_file(path)}


def frame_exponent(frame, target_frac: float, quant: InputQuantConfig,
                   shift_min=-2, shift_max=24) -> tuple[int, bool]:
    target_int = int(round(target_frac * (1 << quant.frac_bits)))
    cfg = CandidateConfig(name="study", mode="bfp_pre_quant",
                          shift_min=shift_min, shift_max=shift_max,
                          target_peak_int=target_int)
    return choose_bfp_shift(frame, cfg, quant, SHIFT_RULE_DEFAULT)


def mel_from_power_float(power: np.ndarray, w_int: np.ndarray,
                         fw: int) -> np.ndarray:
    return power @ (w_int.astype(np.float64) / float(1 << fw)).T


def logs_and_mfcc(mel: np.ndarray, dct: np.ndarray):
    if not np.all(np.isfinite(mel)) or np.any(mel < 0):
        raise ValueError("Mel energies must be finite and non-negative before floor")
    if not np.all(np.isfinite(dct)):
        raise ValueError("DCT coefficients must be finite")
    log = np.log(np.maximum(mel, LOG_FLOOR))
    return log, log @ dct.T


def floor_regression(ref_mel: np.ndarray, cand_mel: np.ndarray) -> int:
    """Cells where the reference is above the floor but the candidate is not."""
    return int(np.sum((ref_mel > LOG_FLOOR) & (cand_mel < LOG_FLOOR)))


# --------------------------------------------------------------- experiments


def run_ablation(windowed, ref_stage, shifts, quant, w_int, fw, dct,
                 twiddle, table=None) -> dict:
    """Part 3: A' / B / C / D on identical inputs and exponents."""
    nf = windowed.shape[0]
    table = table if table is not None else quantize_mel_filterbank(fw)
    if not np.array_equal(w_int, table.weights_int):
        raise ValueError("ablation paths must use the identical Mel table")
    if (quant.width, quant.frac_bits) != (16, 15):
        raise ValueError("ablation D uses the preserved signed16/F15 model")
    spectra = {k: np.zeros((nf, NUM_BINS), dtype=np.complex128)
               for k in ("B", "C", "D")}
    powers = {k: np.zeros((nf, NUM_BINS)) for k in spectra}
    mel_d = np.zeros((nf, w_int.shape[0]))
    ovf_d = 0
    clip = ClipReport()

    for i in range(nf):
        frame = windowed[i].tolist()
        s = int(shifts[i])
        xq, rep = quantize_frame_input(frame, s, quant)
        clip.merge(rep)

        # B: exact float64 DFT of the quantized integer input, no output round
        xi = np.asarray(xq, dtype=np.float64)
        spec = np.fft.rfft(xi, n=NFFT)             # integer-unit spectrum
        # FFT(q) has no core /2**S normalization to undo.
        # q represents u * 2**(s-1) * 2**F.
        fexp = -quant.frac_bits - s + 1
        fb = spec * math.ldexp(1.0, fexp)
        pb = (np.abs(fb) ** 2) / NFFT
        spectra["B"][i], powers["B"][i] = fb, pb

        # C: same, but the final output is rounded to the output grid
        grid = spec / float(1 << S_TOTAL)          # what the core would hold
        # Componentwise round-to-nearest, ties-to-even of the float64 DFT.
        rr = np.rint(grid.real)
        ri = np.rint(grid.imag)
        fc = (rr + 1j * ri) * math.ldexp(
            1.0, S_TOTAL - quant.frac_bits - s + 1)
        pc = (np.abs(fc) ** 2) / NFFT
        spectra["C"][i], powers["C"][i] = fc, pc

        # D: the preserved integer bit model
        re_n, im_n, ov = fft_fixed_natural(xq, [0] * len(xq), *twiddle)
        ovf_d += 1 if ov else 0
        psum = power_integer(re_n[:NUM_BINS], im_n[:NUM_BINS])
        t = mel_accumulate(psum, table)
        pexp = power_exponent(NFFT, S_TOTAL, s, quant.frac_bits)
        mexp = pexp - fw
        mel_d[i] = [float(v) * math.ldexp(1.0, mexp) for v in t]
        spectra["D"][i] = (np.asarray(re_n[:NUM_BINS], dtype=float)
                            + 1j * np.asarray(im_n[:NUM_BINS], dtype=float)) * math.ldexp(
                                1.0, S_TOTAL - quant.frac_bits - s + 1)
        powers["D"][i] = [float(v) * math.ldexp(1.0, pexp) for v in psum]

    # A': Mel coefficient quantization only, from the reference float64 power
    mel_q = mel_from_power_float(ref_stage["power"], w_int, fw)
    mel_b = mel_from_power_float(powers["B"], w_int, fw)
    mel_c = mel_from_power_float(powers["C"], w_int, fw)

    out = {}
    for name, mel, fft, power in (
            ("A_float64_reference", ref_stage["mel_energies"], ref_stage["fft"], ref_stage["power"]),
            ("Aprime_mel_coefficients_only", mel_q, ref_stage["fft"], ref_stage["power"]),
            ("B_input_quant_float_fft", mel_b, spectra["B"], powers["B"]),
            ("C_B_plus_output_rounding", mel_c, spectra["C"], powers["C"]),
            ("D_integer_fft_bitmodel", mel_d, spectra["D"], powers["D"])):
        log, mfcc = logs_and_mfcc(mel, dct)
        if name == "A_float64_reference":
            log, mfcc = ref_stage["log_mel"], ref_stage["mfcc"]
        out[name] = {
            "fft": err(ref_stage["fft"], fft),
            "power": err(ref_stage["power"], power),
            "mel": err(ref_stage["mel_energies"], mel),
            "log_mel": err(ref_stage["log_mel"], log),
            "mfcc": err(ref_stage["mfcc"], mfcc),
            "mel_cells_at_floor": int(np.sum(mel < LOG_FLOOR)),
            "floor_regressions": floor_regression(
                ref_stage["mel_energies"], mel),
            "per_coefficient": coefficient_errors(ref_stage["mfcc"], mfcc),
            "_arrays": {"fft": fft, "power": power, "mel_energies": mel,
                        "log_mel": log, "mfcc": mfcc},
        }
    out["_meta"] = {
        "fft_overflow_frames_D": ovf_d,
        "clipping": clip.as_dict(),
        "physical_fft_shift_S": S_TOTAL,
        "input_fraction_bits": quant.frac_bits,
        "shifts": [int(v) for v in shifts],
        "fft_B_formula": "FFT(q) * 2**(-F-s+1)",
        "fft_C_formula": "rint_components(FFT(q)/2**S) * 2**(S-F-s+1)",
        "power_formula": "abs(FFT_common)**2/N",
        "note": "Errors interact; row differences are not independent "
                "per-stage contributions.",
    }
    return out


def coefficient_errors(ref, got):
    return [{"coefficient": f"C{c}", **err(ref[:, c], got[:, c])}
            for c in range(ref.shape[1])]


def run_width(windowed, ref_stage, target_frac, key, dct, twiddle,
              w_int, fw, table=None) -> dict:
    """Part 4: one width configuration over all frames of one input."""
    in_w, in_f, cfg = WIDTH_CONFIGS[key]
    table = table if table is not None else quantize_mel_filterbank(fw)
    if not np.array_equal(w_int, table.weights_int):
        raise ValueError("width study Mel table mismatch")
    acc_width = bits_for_unsigned(psum_max_for(cfg) * max(table.per_band_weight_sum_int))
    physical_s = physical_fft_shift(NFFT, cfg)
    quant = InputQuantConfig(width=in_w, frac_bits=in_f,
                             clamp_lo=-((1 << (in_w - 1)) - 1),
                             clamp_hi=(1 << (in_w - 1)) - 1)
    promote = cfg.data_frac - in_f
    if promote < 0:
        raise ValueError(f"{key}: input fraction wider than datapath fraction")

    nf = windowed.shape[0]
    mel = np.zeros((nf, w_int.shape[0]))
    fft = np.zeros((nf, NUM_BINS), dtype=np.complex128)
    power = np.zeros((nf, NUM_BINS))
    shifts = np.zeros(nf, dtype=np.int64)
    clip = ClipReport()
    ovf = 0
    clamped = 0
    psum_max_seen = 0
    acc_max_seen = 0
    mel_rows = []
    int_rows = {k: [] for k in ("fft_re", "fft_im", "psum", "input_q")}
    overflow_frame_indices = []

    mexp_base = power_exponent(NFFT, physical_s, 0, cfg.out_f) - fw
    for i in range(nf):
        frame = windowed[i].tolist()
        s, cl = frame_exponent(frame, target_frac, quant)
        clamped += 1 if cl else 0
        shifts[i] = s
        xq, rep = quantize_frame_input(frame, s, quant)
        clip.merge(rep)
        x_int = [v << promote for v in xq]          # exact promotion
        re_n, im_n, ov = fft_fixed_natural_wide(x_int, [0] * len(x_int),
                                                *twiddle, cfg=cfg)
        ovf += 1 if ov else 0
        if ov:
            overflow_frame_indices.append(i)
        psum = power_integer(re_n[:NUM_BINS], im_n[:NUM_BINS],
                             in_width=cfg.out_w)
        psum_max_seen = max(psum_max_seen, max(psum))
        t = mel_accumulate(psum, table, accumulator_width=acc_width)
        acc_max_seen = max(acc_max_seen, max(t))
        mel_rows.append(t)
        mexp = mexp_base - 2 * s
        mel[i] = [float(v) * math.ldexp(1.0, mexp) for v in t]
        fft[i] = (np.asarray(re_n[:NUM_BINS], dtype=float)
                  + 1j * np.asarray(im_n[:NUM_BINS], dtype=float)) * math.ldexp(
                      1.0, physical_s - cfg.out_f - s + 1)
        power[i] = [float(v) * math.ldexp(1.0, mexp + fw) for v in psum]
        int_rows["fft_re"].append(re_n[:NUM_BINS])
        int_rows["fft_im"].append(im_n[:NUM_BINS])
        int_rows["psum"].append(psum)
        int_rows["input_q"].append(xq)

    log, mfcc = logs_and_mfcc(mel, dct)
    hist = {}
    for v in shifts.tolist():
        hist[str(v)] = hist.get(str(v), 0) + 1
    return {
        "config": cfg.describe(),
        "input_width": in_w, "input_frac": in_f,
        "promote_shift": promote,
        "physical_fft_shift_S": physical_s,
        "output_requant_shift_bits": output_requant_shift(cfg),
        "integer_code_shift_bits": integer_code_shift(NFFT, cfg),
        "power_exponent_at_s0": mexp_base + fw,
        "mel_exponent_at_s0": mexp_base,
        "stages": {"fft": err(ref_stage["fft"], fft),
                   "power": err(ref_stage["power"], power),
                   "mel_energies": err(ref_stage["mel_energies"], mel),
                   "log_mel": err(ref_stage["log_mel"], log),
                   "mfcc": err(ref_stage["mfcc"], mfcc)},
        "per_coefficient": coefficient_errors(ref_stage["mfcc"], mfcc),
        "mel_cells_at_floor": int(np.sum(mel < LOG_FLOOR)),
        "floor_regressions": floor_regression(ref_stage["mel_energies"], mel),
        "fft_overflow_frames": ovf,
        "fft_overflow_frame_indices": overflow_frame_indices,
        "shift_clamped_frames": clamped,
        "shift_histogram": {k: hist[k] for k in sorted(hist, key=int)},
        "clipping": clip.as_dict(),
        "widths": {
            "psum_max_theoretical": psum_max_for(cfg),
            "psum_width_unsigned": bits_for_unsigned(psum_max_for(cfg)),
            "psum_max_observed": int(psum_max_seen),
            "square_full_product_width_signed": 2 * cfg.out_w,
            "mel_weight_width_unsigned": fw + 1,
            "mel_product_full_width_unsigned": bits_for_unsigned(psum_max_for(cfg)) + fw + 1,
            "mel_product_tight_width_unsigned": bits_for_unsigned(psum_max_for(cfg) * (1 << fw)),
            "mel_accumulator_width_unsigned": acc_width,
            "mel_accumulator_max_observed": int(acc_max_seen),
            "mel_accumulator_fits_int64": acc_max_seen <= (1 << 63) - 1,
        },
        "_integer_arrays": {**int_rows, "mel": mel_rows,
                            "shift": [[int(s)] for s in shifts]},
        "_arrays": {"fft": fft, "power": power, "mel_energies": mel,
                    "log_mel": log, "mfcc": mfcc},
    }



def load_checked_arrays(directory, required_shapes=None):
    """Require real hashes, exact bytes/shapes, declared dtype and finite values."""
    meta_path = directory / "arrays.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    arrays, checked = {}, {}
    for name, info in meta["arrays"].items():
        fp = directory / info["file"]
        digest = sha256_file(fp)
        if digest != info.get("sha256"):
            raise RuntimeError(f"reference sha256 missing/mismatch: {fp}")
        dtype = np.dtype(info["dtype"])
        if dtype.str not in ("<f8", "<c16", "<i8"):
            raise RuntimeError(f"unsupported reference dtype: {fp}: {dtype}")
        shape = tuple(info["shape"])
        expected_bytes = math.prod(shape) * dtype.itemsize
        if fp.stat().st_size != expected_bytes or info.get("bytes") != expected_bytes:
            raise RuntimeError(f"reference byte/shape mismatch: {fp}")
        a = load_bin(fp, info["dtype"], shape)
        if not np.all(np.isfinite(a)):
            raise RuntimeError(f"non-finite reference array: {fp}")
        arrays[name] = a
        checked[name] = {"file": str(fp), "actual_sha256": digest,
                         "recorded_sha256": info["sha256"], "matches": True,
                         "shape": list(shape), "dtype": dtype.str,
                         "bytes": expected_bytes}
    for name, shape in (required_shapes or {}).items():
        if name not in arrays or arrays[name].shape != shape:
            raise RuntimeError(f"{directory}: required {name} shape {shape}")
    return arrays, {"metadata_sha256": sha256_file(meta_path), "arrays": checked}


def validate_case_arrays(arr, samples):
    nf = max(0, 1 + (samples - NFFT) // 160)
    shapes = {"input_float": (samples,), "preemphasis": (samples,),
              "frame_starts": (nf,), "frames": (nf, NFFT),
              "windowed": (nf, NFFT), "fft": (nf, NUM_BINS),
              "power": (nf, NUM_BINS), "mel_energies": (nf, 26),
              "log_mel": (nf, 26), "mfcc": (nf, 13)}
    for name in REQUIRED_ARRAYS:
        if name not in arr or arr[name].shape != shapes[name]:
            raise RuntimeError(f"missing/wrong shape: {name}, expected {shapes[name]}")
        expected_dtype = "<i8" if name == "frame_starts" else "<c16" if name == "fft" else "<f8"
        if arr[name].dtype.str != expected_dtype:
            raise RuntimeError(f"wrong dtype for {name}: {arr[name].dtype}")
        if not np.all(np.isfinite(arr[name])):
            raise ValueError(f"non-finite case array: {name}")
    if np.any(arr["power"] < 0):
        raise ValueError("negative reference power")
    logs_and_mfcc(arr["mel_energies"], np.zeros((13, 26)))


def save_float_arrays(path, arrays):
    for name, a in arrays.items():
        if not np.all(np.isfinite(a)):
            raise ValueError(f"non-finite output array: {name}")
    np.savez_compressed(path, **arrays)
    return {"file": path.name, "sha256": sha256_file(path),
            "arrays": {k: {"shape": list(v.shape), "dtype": v.dtype.str}
                       for k, v in arrays.items()}}


def collect_cases(ref_run, want, out_dir, profile, coeff):
    """Read only development/synthetic; never enumerate or load evaluation."""
    window, mel, dct = coeff["window"], coeff["mel_filters"], coeff["dct_matrix"]
    locations = []
    if "dev" in want:
        locations.append((DEV_ID, "development", ref_run / "development" / DEV_ID))
    if "synthetic" in want:
        locations.extend((p.name, "synthetic", p) for p in
                         sorted((ref_run / "synthetic").iterdir()) if p.is_dir())
    cases, checks = [], []
    for cid, group, base in locations:
        arr, hashes = load_checked_arrays(base / PROFILE)
        pcm_path = base / "input_s16le.pcm"
        if pcm_path.stat().st_size % 2:
            raise RuntimeError(f"odd PCM byte count: {pcm_path}")
        pcm = np.fromfile(pcm_path, dtype="<i2")
        validate_case_arrays(arr, pcm.size)
        if group == "development" and (pcm.size != 85920 or arr["mfcc"].shape != (534, 13)):
            raise RuntimeError("development input must be all 85920 samples / 534 frames")
        fe = float64_frontend(pcm, window, 512, 160, 0.95, 32768.0)
        check = {"id": cid, "pcm_to_window": {}, "fft_onwards": {}}
        for name, a in zip(REQUIRED_ARRAYS[:5], fe):
            check["pcm_to_window"][name] = err(arr[name], a)
            if not np.array_equal(arr[name], a):
                raise RuntimeError(f"PCM front-end differs from frozen {cid}/{name}")
        regenerated = float64_reference_stages(arr["windowed"], mel, dct, NFFT, NFFT)
        for name, a in regenerated.items():
            check["fft_onwards"][name] = err(arr[name], a)
            # Compare linear stages directly. Near-zero Mel energies amplify
            # NumPy-vs-SciPy FFT roundoff through log, so verify each nonlinear
            # equation from its frozen input instead of relaxing a log tolerance.
            if name in ("fft", "power", "mel_energies") and not np.allclose(
                    arr[name], a, rtol=1e-12, atol=2e-12):
                raise RuntimeError(f"float64 selfcheck differs: {cid}/{name}")
        frozen_input_log, _ = logs_and_mfcc(arr["mel_energies"], dct)
        frozen_input_mfcc = arr["log_mel"] @ dct.T
        for name, a in (("log_mel", frozen_input_log), ("mfcc", frozen_input_mfcc)):
            check.setdefault("frozen_input_equation_check", {})[name] = err(arr[name], a)
            if not np.allclose(arr[name], a, rtol=1e-12, atol=2e-12):
                raise RuntimeError(f"float64 equation selfcheck differs: {cid}/{name}")
        checks.append(check)
        saved_pcm = out_dir / "arrays" / f"{cid}_s16le.pcm"
        shutil.copy2(pcm_path, saved_pcm)
        cases.append({"id": cid, "group": group, "source": "frozen_reference_run",
                      "arrays": arr, "reference_hashes": hashes,
                      "pcm": {"source": str(pcm_path), "file": saved_pcm.name,
                              "sha256": sha256_file(pcm_path), "samples": int(pcm.size)}})
    if "extra" in want:
        for extra in build_extra_inputs(window, 512, 160):
            pcm, cid = extra["pcm"], extra["id"]
            fe = float64_frontend(pcm, window, 512, 160, 0.95, 32768.0)
            arr = dict(zip(REQUIRED_ARRAYS[:5], fe))
            arr.update(float64_reference_stages(fe[-1], mel, dct, NFFT, NFFT))
            validate_case_arrays(arr, pcm.size)
            pcm_path = out_dir / "arrays" / f"{cid}_s16le.pcm"
            pcm.astype("<i2").tofile(pcm_path)
            cases.append({"id": cid, "group": "extra_synthetic",
                          "source": "generated_by_snapshotted_run_fixed_pilot_helpers",
                          "arrays": arr, "generator_seed": EXTRA_SEED, "note": extra["note"],
                          "generated_reference": save_float_arrays(
                              out_dir / "arrays" / f"{cid}_float64_reference.npz", arr),
                          "pcm": {"file": pcm_path.name, "sha256": sha256_file(pcm_path),
                                  "samples": int(pcm.size)}})
    counts = {g: sum(c["group"] == g for c in cases)
              for g in ("development", "synthetic", "extra_synthetic")}
    expected = {"development": int("dev" in want),
                "synthetic": 17 if "synthetic" in want else 0,
                "extra_synthetic": 6 if "extra" in want else 0}
    if counts != expected or len({c["id"] for c in cases}) != len(cases):
        raise RuntimeError(f"unexpected input set {counts}, expected {expected}")
    return cases, checks, counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--reference-run", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--fw", type=int, default=16)
    ap.add_argument("--configs", default=",".join(WIDTH_CONFIGS))
    ap.add_argument("--inputs", default="dev,synthetic,extra")
    args = ap.parse_args()
    keys = [k.strip() for k in args.configs.split(",") if k.strip()]
    want = {w.strip() for w in args.inputs.split(",") if w.strip()}
    if not keys or len(set(keys)) != len(keys) or any(k not in WIDTH_CONFIGS for k in keys):
        ap.error("configs must be a nonempty unique list of supported width candidates")
    if not want or not want <= {"dev", "synthetic", "extra"}:
        ap.error("inputs may contain only dev,synthetic,extra (evaluation prohibited)")
    if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
        ap.error("run-id must be one folder name")
    if not 1 <= args.fw <= 30:
        ap.error("fw must be in 1..30; integer table storage is checked")
    out_dir = args.out_root / args.run_id
    if out_dir.exists():
        print(f"FAIL: output folder already exists: {out_dir}")
        print("Experiment records are immutable. Use a new --run-id.")
        return 1
    (out_dir / "arrays").mkdir(parents=True, exist_ok=False)
    snap = out_dir / "source_snapshot"
    snap.mkdir()
    sources = list((ROOT / "software" / "fixed_model").glob("*.py"))
    sources += list((ROOT / "verification" / "fixed").glob("*.py"))
    sources += [Path(__file__).resolve(), ROOT / "scripts" / "run_fixed_pilot.py",
                ROOT / "software" / "fixed_model" / "PROVENANCE.json",
                ROOT / "software" / "fixed_model" / "coefficients" / "twiddle_1024_w16.mem",
                ROOT / "docs" / "MFCC_SPEC.md"]
    source_hashes = {}
    for src in sorted(sources):
        rel = src.relative_to(ROOT)
        dst = snap / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        source_hashes[rel.as_posix()] = sha256_file(dst)
    preserved_hash = "87bb1ea7d956ba8ea91b3e635d77c53739b387a313522809a38621cd25a0438a"
    if source_hashes["software/fixed_model/fft_bitmodel.py"] != preserved_hash:
        raise RuntimeError("preserved FFT model hash changed")
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    ref_run = args.reference_run
    profile_path = ref_run / "profiles" / f"{PROFILE}.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    expected_profile = {"profile_id": PROFILE, "sample_rate": 16000,
                        "frame_length": 512, "frame_step": 160, "nfft": 512,
                        "num_filters": 26, "num_ceps": 13, "preemphasis": 0.95,
                        "pcm_divisor": 32768.0, "initial_previous_sample": 0.0,
                        "frame_policy": "full_frames_only", "window": "symmetric_hamming",
                        "fft_norm": "backward", "fft_sign": "negative",
                        "power_divisor": 512, "one_sided_double": False,
                        "mel_scale": "htk", "lowfreq": 0.0, "highfreq": 8000.0,
                        "mel_bin_rule": "floor((nfft+1)*hz/sample_rate)",
                        "mel_normalization": "peak_one_no_area_normalization",
                        "log_floor": LOG_FLOOR, "log_base": "natural", "log_policy": "floor",
                        "dct_type": 2, "dct_norm": "ortho", "lifter": 0,
                        "append_energy": False, "delta": False, "delta_delta": False,
                        "cmvn": False, "output_order": [f"C{i}" for i in range(13)]}
    for k, expected in expected_profile.items():
        if profile.get(k) != expected:
            raise RuntimeError(f"reference profile mismatch: {k}={profile.get(k)}, expected {expected}")
    shutil.copy2(profile_path, snap / f"{PROFILE}.json")
    coeff, coeff_hashes = load_checked_arrays(ref_run / "coefficients" / PROFILE,
                                              {"window": (512,), "mel_filters": (26, 257),
                                               "mel_edges": (28,), "dct_matrix": (13, 26)})
    for name, a in coeff.items():
        if a.dtype.str != ("<i8" if name == "mel_edges" else "<f8"):
            raise RuntimeError(f"wrong coefficient dtype: {name}")
    table = quantize_mel_filterbank(args.fw)
    if not np.array_equal(np.asarray(table.weights_float), coeff["mel_filters"]):
        raise RuntimeError("regenerated Mel table differs from frozen coefficients")
    if not np.array_equal(np.asarray(table.edges), coeff["mel_edges"]):
        raise RuntimeError("regenerated Mel bin edges differ")
    w_int = np.asarray(table.weights_int, dtype="<i8")
    coeff_dump = save_int_array(out_dir / "arrays" / "mel_weights_i64.bin", table.weights_int)
    frozen_coeff_dump = save_float_arrays(out_dir / "arrays" / "frozen_coefficients.npz", coeff)
    dct = coeff["dct_matrix"]
    rom = ROOT / "software" / "fixed_model" / "coefficients" / "twiddle_1024_w16.mem"
    twiddle = load_twiddle_rom(str(rom))
    cases, selfchecks, counts = collect_cases(ref_run, want, out_dir, profile, coeff)
    results, ablations = [], []
    t0 = time.time()
    for case in cases:
        cid, group, arr = case["id"], case["group"], case["arrays"]
        windowed = arr["windowed"]
        ref_stage = {k: arr[k] for k in ("fft", "power", "mel_energies", "log_mel", "mfcc")}
        entry = {"id": cid, "group": group, "frames": int(windowed.shape[0]), "targets": {}}
        for tname, tfrac in TARGETS.items():
            per_cfg = {}
            base_quant = InputQuantConfig()
            shifts = [frame_exponent(frame.tolist(), tfrac, base_quant)[0] for frame in windowed]
            for key in keys:
                r = run_width(windowed, ref_stage, tfrac, key, dct, twiddle,
                              w_int, args.fw, table=table)
                prefix = f"{cid}_{tname}_{key}"
                r["shift_diff_vs_input16_frames"] = sum(
                    row[0] != s for row, s in zip(r["_integer_arrays"]["shift"], shifts))
                r["integer_dumps"] = {name: save_int_array(
                    out_dir / "arrays" / f"{prefix}_{name}.bin", rows,
                    columns={"input_q": NFFT, "fft_re": NUM_BINS, "fft_im": NUM_BINS,
                             "psum": NUM_BINS, "mel": 26, "shift": 1}[name])
                    for name, rows in r.pop("_integer_arrays").items()}
                r["stage_dump"] = save_float_arrays(out_dir / "arrays" / f"{prefix}_stages.npz",
                                                     r.pop("_arrays"))
                per_cfg[key] = r
                if group == "development":
                    print(f"  [development] {tname}/{key}: "
                          f"MFCC RMSE={r['stages']['mfcc']['rmse']:.6g}", flush=True)
            entry["targets"][tname] = per_cfg
            ab = run_ablation(windowed, ref_stage, shifts, base_quant, w_int,
                              args.fw, dct, twiddle, table=table)
            for name, path in ab.items():
                if name == "_meta":
                    continue
                path["stage_dump"] = save_float_arrays(
                    out_dir / "arrays" / f"{cid}_{tname}_{name}_stages.npz", path.pop("_arrays"))
            ab.update({"id": cid, "group": group, "frames": int(windowed.shape[0]),
                       "target": tname, "target_fraction": tfrac})
            # Both D implementations must agree (within float64 log evaluation roundoff).
            if "d16" in per_cfg:
                d = ab["D_integer_fft_bitmodel"]["mfcc"]
                base = per_cfg["d16"]["stages"]["mfcc"]
                if not all(math.isclose(d[k], base[k], rel_tol=1e-12, abs_tol=1e-12) for k in d):
                    raise RuntimeError("preserved and wide-baseline pipeline metrics disagree")
            ablations.append(ab)
        results.append(entry)
        print(f"  [{group:16s}] {cid:28s} frames={entry['frames']:4d} done", flush=True)
    elapsed = time.time() - t0
    with open(out_dir / "width_comparison.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["input_id", "group", "frames", "target", "config", "input_W/F",
                         "internal_W/F", "output_W/F", "twiddle_W/F", "physical_fft_shift_S",
                         "output_requant_shift_bits", "integer_code_shift_bits", "stage",
                         "max_abs", "rmse", "floor_regressions", "fft_overflow_frames",
                         "clipped_frame_samples", "clipping_events", "psum_width", "mel_acc_width"])
        for e in results:
            for target, per in e["targets"].items():
                for key, r in per.items():
                    c = r["config"]
                    for stage, stats in r["stages"].items():
                        writer.writerow([e["id"], e["group"], e["frames"], target, key,
                                         f"{r['input_width']}/{r['input_frac']}",
                                         f"{c['data_width']}/{c['data_frac']}",
                                         f"{c['output_width']}/{c['output_frac']}",
                                         f"{c['twiddle_width']}/{c['twiddle_frac']}",
                                         r["physical_fft_shift_S"], r["output_requant_shift_bits"],
                                         r["integer_code_shift_bits"], stage, stats["max_abs"], stats["rmse"],
                                         r["floor_regressions"], r["fft_overflow_frames"],
                                         r["clipping"]["clipped_frame_samples"], r["clipping"]["clip_events_total"],
                                         r["widths"]["psum_width_unsigned"],
                                         r["widths"]["mel_accumulator_width_unsigned"]])
    with open(out_dir / "mfcc_coefficient_errors.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["input_id", "target", "config", "coefficient", "max_abs", "rmse"])
        for e in results:
            for target, per in e["targets"].items():
                for key, r in per.items():
                    for c in r["per_coefficient"]:
                        writer.writerow([e["id"], target, key, c["coefficient"], c["max_abs"], c["rmse"]])
    # Protect the meaning of the snapshot if another session edits shared inputs during a run.
    for rel, digest in source_hashes.items():
        if sha256_file(ROOT / rel) != digest:
            raise RuntimeError(f"source changed during run: {rel}; keep this run, use a new id")
    manifest = {
        "schema_version": 2, "run_id": args.run_id, "status": "completed",
        "started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(elapsed, 1), "experiment_class": "mixed_precision",
        "experiment_class_note": "float64 pre-emphasis/framing/window and log/DCT; integer FFT input, FFT, Power/Mel. No new RTL.",
        "command": [sys.executable, *sys.argv],
        "environment": {"python": sys.version.split()[0], "numpy": np.__version__,
                        "platform": platform.platform(), "thread_env": {k: os.environ.get(k) for k in
                            ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}},
        "profile": {"file": str(profile_path), "sha256": sha256_file(profile_path), "settings": profile},
        "reference_run": str(ref_run), "reference_coefficients": coeff_hashes,
        "frozen_coefficient_dump": frozen_coeff_dump,
        "input_counts": counts, "total_frames_per_candidate": sum(e["frames"] for e in results),
        "inputs": [{k: v for k, v in c.items() if k != "arrays"} for c in cases],
        "frontend_selfchecks": selfchecks,
        "source_snapshot_sha256": source_hashes,
        "preserved_fft_sha256": preserved_hash,
        "bfp_shift_rule": SHIFT_RULE_DEFAULT, "bfp_shift_limits": [-2, 24], "silence_shift": 0,
        "input_quantization": "ties-to-even; saturate signed W then clamp to symmetric +/-(2**(W-1)-1); promote F exactly by left shift",
        "targets": TARGETS,
        "target_integer_by_input_frac": {str(f): {t: int(round(v*(1 << f))) for t, v in TARGETS.items()}
                                         for f in (15, 17, 19)},
        "mel_fw": args.fw, "mel_table": table.manifest(), "mel_integer_dump": coeff_dump,
        "twiddle": {"file": str(rom), "sha256": sha256_file(rom), "W": 16, "F": 15},
        "scales": {"physical_fft_shift_S": 9,
                   "fft_common": "(re+j*im)*2**(S-F_out-s+1)",
                   "power": "psum*2**(2*S+2-2*s-2*F_out-log2(N))",
                   "mel": "T*2**(P_exp-Fw)",
                   "floor": LOG_FLOOR, "floor_regression": "reference > floor AND candidate < floor",
                   "output_F_conversion": "recorded separately; never added again to physical S"},
        "part3_ablation_results": ablations,
        "part3_ablation_development": [a for a in ablations if a["id"] == DEV_ID],
        "part4_width_results": results,
        "limits": ["Row error differences are not independent per-stage contributions.",
                   "Observed zero overflow does not prove safety for every input.",
                   "No fixed acceptance tolerances have been established.",
                   "No new RTL, synthesis, resource/timing/power/board measurements.",
                   "Evaluation speech (20 clips) was not opened; no recognition accuracy measured."]}
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    artifact_hashes = {p.relative_to(out_dir).as_posix(): sha256_file(p)
                       for p in sorted(out_dir.rglob("*")) if p.is_file()}
    (out_dir / "artifact_hashes.json").write_text(json.dumps(artifact_hashes, indent=2), encoding="utf-8")
    print(f"run_id: {args.run_id}\noutput: {out_dir}\ncases: {len(results)}\nelapsed: {elapsed:.1f} s")
    print("RESULT: COMPLETED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
