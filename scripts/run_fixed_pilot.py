#!/usr/bin/env python3
"""Q1 pilot runner: mixed-precision fixed-point candidates vs the float64 truth.

What this is
------------
A **mixed-precision numerical experiment**, not a finished fixed-point MFCC:

    float64 pre-emphasis / framing / window   (shared with the reference)
    -> FFT input quantization, signed 16 / F15 (integer)
    -> reused FFT bit model                    (integer, wrap, ties-to-even)
    -> integer Power and Mel accumulation      (integer, exact)
    -> float64 log floor and orthonormal DCT   (NOT quantized; that is Q2)

Candidates
----------
A  reference      frozen float64 arrays, read only
B  fixed_alpha_half  s = 0 every frame, FFT input = quantize(u/2)
C  bfp_pre_quant     s chosen from the float64 frame peak, then one quantization
D  q15_then_shift    auxiliary: quantize at alpha = 1, then shift integers left

Nothing in the frozen reference run, the shared PCM, the coefficient sources or
the previous FFT original is written.  Outputs go to
``<out-root>/<run-id>`` only.

Usage
-----
  python scripts/run_fixed_pilot.py
  python scripts/run_fixed_pilot.py --max-frames 32 --no-figures

Omitting --run-id generates a timestamped id; use a fresh id on every invocation.
Existing output directories are rejected, including a timestamp collision.
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
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from software.fixed_model.coeffs import (coefficient_error_sweep,  # noqa: E402
                                         quantize_mel_filterbank)
from software.fixed_model.fft_bitmodel import load_twiddle_rom  # noqa: E402
from software.fixed_model.pipeline import (CANDIDATES,  # noqa: E402
                                           FFT_TOTAL_SHIFT_S, NUM_BINS,
                                           ClipReport, InputQuantConfig,
                                           SHIFT_RULE_DEFAULT, dct_ortho,
                                           run_frame)

DEFAULT_REFERENCE = Path(r"D:\2610_MFCC\build\python_reference\reproduce_01")
DEFAULT_OUT_ROOT = Path(r"D:\2610_MFCC\build\fixed_pilot")
DEV_ID = "8463-294828-0037"
PROFILE = "comparison_raw13"
LOG_FLOOR = 1e-12
EXTRA_SEED = 20261004


# ------------------------------------------------------------------ helpers


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_array(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def load_bin(path: Path, dtype: str, shape) -> np.ndarray:
    return np.fromfile(path, dtype=dtype).reshape(shape)


REQUIRED_ARRAYS = ("input_float", "preemphasis", "frame_starts", "frames",
                   "windowed", "fft", "power", "mel_energies", "log_mel",
                   "mfcc")


class ReferenceError(RuntimeError):
    """A frozen reference artefact is missing, mis-shaped or corrupt."""


def load_reference_case(case_dir: Path, *, strict: bool = True) -> dict:
    """Load the frozen float64 stage arrays for one input, read only.

    Strict by design (review fixed_review_20261004): a missing file, a shape
    that disagrees with arrays.json, a SHA-256 that disagrees with the recorded
    value, or a non-finite element raises instead of being skipped.
    """
    meta_path = case_dir / "arrays.json"
    if not meta_path.is_file():
        raise ReferenceError(f"missing arrays.json: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    arrays, hashes = {}, {}
    for name, info in meta["arrays"].items():
        fp = case_dir / info["file"]
        if not fp.is_file():
            raise ReferenceError(f"missing array file: {fp}")
        digest = sha256_file(fp)
        recorded = info.get("sha256")
        if strict and recorded and digest != recorded:
            raise ReferenceError(
                f"sha256 mismatch for {fp}: recorded {recorded}, actual {digest}")
        hashes[name] = {"file": info["file"], "recorded_sha256": recorded,
                        "actual_sha256": digest,
                        "matches": (recorded is None or digest == recorded)}
        want = tuple(info["shape"])
        raw = np.fromfile(fp, dtype=info["dtype"])
        expected_elems = 1
        for d in want:
            expected_elems *= d
        if raw.size != expected_elems:
            raise ReferenceError(
                f"shape mismatch for {fp}: file holds {raw.size} elements, "
                f"arrays.json declares {want} ({expected_elems})")
        a = raw.reshape(want)
        if a.size and not np.all(np.isfinite(a.view(np.float64)
                                             if np.iscomplexobj(a) else a)):
            raise ReferenceError(f"non-finite value in {fp}")
        arrays[name] = a
    if strict:
        missing = [n for n in REQUIRED_ARRAYS if n not in arrays]
        if missing:
            raise ReferenceError(
                f"{case_dir}: required arrays missing from arrays.json: {missing}")
    return {"meta": meta, "arrays": arrays, "hashes": hashes}


def err_stats(ref: np.ndarray, got: np.ndarray) -> dict:
    ref = np.asarray(ref, dtype=np.complex128 if np.iscomplexobj(ref)
                     else np.float64)
    got = np.asarray(got, dtype=ref.dtype)
    d = np.abs(got - ref)
    denom = np.abs(ref)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(denom > 0, d / denom, np.nan)
    return {
        "max_abs": float(np.max(d)) if d.size else 0.0,
        "rmse": float(np.sqrt(np.mean(d.astype(np.float64) ** 2)))
        if d.size else 0.0,
        "mean_abs": float(np.mean(d)) if d.size else 0.0,
        "max_rel_where_ref_nonzero": (float(np.nanmax(rel))
                                      if d.size and np.any(denom > 0)
                                      else None),
        "argmax_flat_index": int(np.argmax(d)) if d.size else -1,
        "elements": int(d.size),
    }


# ------------------------------------------------- extra synthetic inputs


def build_extra_inputs(window: np.ndarray, frame_length: int,
                       frame_step: int) -> list[dict]:
    """Inputs that target BFP exponent transitions and tiny amplitudes.

    These are generated here, so their float64 reference is produced by this
    script rather than by the frozen reference run.  They are kept in a
    separate list with their own hashes for exactly that reason.
    """
    rng = np.random.default_rng(EXTRA_SEED)
    n_total = frame_length + 6 * frame_step        # 7 full frames
    out = []

    def add(name, pcm_int16, note):
        pcm = np.asarray(pcm_int16, dtype=np.int64)
        pcm = np.clip(pcm, -32768, 32767).astype(np.int16)
        out.append({"id": name, "pcm": pcm, "note": note})

    # amplitude stepping down by exactly one octave per frame: forces the BFP
    # exponent to change between consecutive frames
    ramp = np.zeros(n_total, dtype=np.float64)
    for f in range(7):
        a = 0.5 ** f
        lo = f * frame_step
        hi = min(n_total, lo + frame_step)
        t = np.arange(lo, hi)
        ramp[lo:hi] = a * np.sin(2 * np.pi * 1000.0 * t / 16000.0)
    add("bfp_octave_steps", np.round(ramp * 32767),
        "one octave amplitude step per hop; exercises BFP exponent changes")

    # amplitude just above / just below a power of two, so rounding of the
    # exponent decision matters
    edge = np.zeros(n_total, dtype=np.float64)
    for f in range(7):
        a = (0.5 ** (f // 2)) * (1.0 - 1e-4 if f % 2 else 1.0 + 1e-4)
        a = min(a, 1.0)
        lo = f * frame_step
        hi = min(n_total, lo + frame_step)
        t = np.arange(lo, hi)
        edge[lo:hi] = a * np.sin(2 * np.pi * 997.0 * t / 16000.0)
    add("bfp_exponent_edges", np.round(edge * 32767),
        "amplitudes straddling powers of two; exponent decision boundary")

    # a single LSB of PCM, i.e. the smallest non-silent input
    one = np.zeros(n_total, dtype=np.float64)
    one[::97] = 1.0
    add("pcm_one_lsb_sparse", one,
        "isolated +-1 LSB samples; smallest non-silent amplitude")

    # tiny alternating, below the fixed alpha=1/2 quantization step
    alt = np.where(np.arange(n_total) % 2 == 0, 1, -1).astype(np.float64)
    add("pcm_one_lsb_alternating", alt,
        "+-1 LSB alternating; Nyquist at the quantization floor")

    # silence followed by full scale: exponent must track a hard transition
    mix = np.zeros(n_total, dtype=np.float64)
    mix[n_total // 2:] = 32767.0 * np.sin(
        2 * np.pi * 1000.0 * np.arange(n_total - n_total // 2) / 16000.0) / 32767.0
    add("silence_to_fullscale", np.round(mix * 32767),
        "silent frames then full scale; exponent transition from silence")

    # low-amplitude speech-like noise at -60 dBFS
    q = rng.normal(0.0, 1.0, n_total)
    q = q / np.max(np.abs(q)) * (10 ** (-60 / 20))
    add("noise_minus60dbfs", np.round(q * 32767),
        "noise at -60 dBFS; deep in the quantization-limited regime")
    return out


def float64_frontend(pcm_int16: np.ndarray, window: np.ndarray,
                     frame_length: int, frame_step: int,
                     preemph: float, divisor: float):
    """float64 pre-emphasis -> full frames only -> window, per MFCC_SPEC."""
    x = np.asarray(pcm_int16, dtype=np.float64) / divisor
    y = np.empty_like(x)
    if x.size:
        y[0] = x[0]
        y[1:] = x[1:] - preemph * x[:-1]
    if x.size < frame_length:
        starts = np.zeros(0, dtype=np.int64)
    else:
        n = 1 + (x.size - frame_length) // frame_step
        starts = np.arange(n, dtype=np.int64) * frame_step
    frames = np.stack([y[s:s + frame_length] for s in starts]) \
        if starts.size else np.zeros((0, frame_length))
    return x, y, starts, frames, frames * window


def float64_reference_stages(windowed: np.ndarray, mel_float: np.ndarray,
                            dct_matrix: np.ndarray, nfft: int,
                            power_divisor: float):
    fft = np.fft.rfft(windowed, n=nfft, axis=1)
    power = (np.abs(fft) ** 2) / power_divisor
    mel = power @ mel_float.T
    log_mel = np.log(np.maximum(mel, LOG_FLOOR))
    mfcc = log_mel @ dct_matrix.T
    return {"fft": fft, "power": power, "mel_energies": mel,
            "log_mel": log_mel, "mfcc": mfcc}


# ------------------------------------------------------------------ candidate


def run_candidate(windowed: np.ndarray, cand_name: str, table, twiddle,
                  dct_matrix: np.ndarray, quant: InputQuantConfig) -> dict:
    cfg = CANDIDATES[cand_name]
    nframes = windowed.shape[0]
    shift = np.zeros(nframes, dtype=np.int64)
    fft_c = np.zeros((nframes, NUM_BINS), dtype=np.complex128)
    power = np.zeros((nframes, NUM_BINS), dtype=np.float64)
    psum = np.zeros((nframes, NUM_BINS), dtype=np.int64)
    mel_int = np.zeros((nframes, table.num_filters), dtype=object)
    mel = np.zeros((nframes, table.num_filters), dtype=np.float64)
    log_mel = np.zeros((nframes, table.num_filters), dtype=np.float64)
    floored = np.zeros((nframes, table.num_filters), dtype=bool)
    dq_err_max = 0.0
    dq_err_sq = 0.0
    dq_n = 0
    clip_total = ClipReport()
    ovf_frames = 0
    clamped_frames = 0
    counters: dict[str, int] = {}

    for i in range(nframes):
        frame = windowed[i].tolist()
        r = run_frame(frame, cfg, table, twiddle, quant, LOG_FLOOR)
        shift[i] = r.shift_s
        sc = r.scales
        # FFT output in common units: DFT(u) = r_int * 2**(S - 14 - s)
        fexp = FFT_TOTAL_SHIFT_S - 14 - r.shift_s
        scale = math.ldexp(1.0, fexp)
        fft_c[i] = (np.asarray(r.fft_re, dtype=np.float64)
                    + 1j * np.asarray(r.fft_im, dtype=np.float64)) * scale
        psum[i] = np.asarray(r.psum, dtype=np.int64)
        power[i] = np.asarray(r.psum, dtype=np.float64) * \
            math.ldexp(1.0, sc.power_exp_log2)
        mel_int[i] = r.mel_int
        mel[i] = [float(t) * math.ldexp(1.0, sc.mel_exp_log2)
                  for t in r.mel_int]
        log_mel[i] = r.log_mel
        floored[i] = r.mel_floored
        clip_total.merge(r.clip)
        ovf_frames += 1 if r.fft_overflow else 0
        clamped_frames += 1 if r.shift_clamped else 0
        for k, v in r.counter.as_dict().items():
            counters[k] = counters.get(k, 0) + v
        # input quantization loss: float64 windowed value vs the value the
        # integer input actually represents
        back = np.asarray(r.fft_in, dtype=np.float64) / 32768.0 / \
            math.ldexp(1.0, r.shift_s - 1)
        d = np.abs(back - windowed[i])
        dq_err_max = max(dq_err_max, float(np.max(d)))
        dq_err_sq += float(np.sum(d ** 2))
        dq_n += d.size

    mfcc = dct_ortho(log_mel, dct_matrix) if nframes else \
        np.zeros((0, dct_matrix.shape[0]))
    return {
        "candidate": cand_name,
        "mode": cfg.mode,
        "description": cfg.description,
        "shift_s": shift,
        "fft": fft_c,
        "psum": psum,
        "power": power,
        "mel_int": mel_int,
        "mel_energies": mel,
        "log_mel": log_mel,
        "mel_floored": floored,
        "mfcc": mfcc,
        "input_quantization_loss": {
            "max_abs": dq_err_max,
            "rmse": math.sqrt(dq_err_sq / dq_n) if dq_n else 0.0,
            "samples": dq_n,
        },
        "clipping": clip_total.as_dict(),
        "fft_overflow_frames": ovf_frames,
        "shift_clamped_frames": clamped_frames,
        "overflow_counters": dict(sorted(counters.items())),
    }


def summarize(ref: dict, cand: dict, table) -> dict:
    stages = {}
    for name in ("fft", "power", "mel_energies", "log_mel", "mfcc"):
        if name in ref and ref[name].shape == cand[name].shape:
            stages[name] = err_stats(ref[name], cand[name])
    per_coef = []
    if "mfcc" in ref and ref["mfcc"].shape == cand["mfcc"].shape \
            and ref["mfcc"].size:
        for c in range(ref["mfcc"].shape[1]):
            d = np.abs(cand["mfcc"][:, c] - ref["mfcc"][:, c])
            per_coef.append({
                "coefficient": f"C{c}",
                "max_abs": float(np.max(d)),
                "rmse": float(np.sqrt(np.mean(d ** 2))),
                "frame_of_max": int(np.argmax(d)),
            })
    s = cand["shift_s"]
    hist = {}
    for v in s.tolist():
        hist[str(v)] = hist.get(str(v), 0) + 1
    zero_mel = int(np.sum(np.asarray(
        [[int(t) == 0 for t in row] for row in cand["mel_int"]], dtype=bool))) \
        if len(cand["mel_int"]) else 0
    return {
        "stages": stages,
        "per_coefficient": per_coef,
        "shift_histogram": dict(sorted(hist, key=lambda k: int(k))) if False
        else {k: hist[k] for k in sorted(hist, key=lambda k: int(k))},
        "shift_min": int(s.min()) if s.size else None,
        "shift_max": int(s.max()) if s.size else None,
        "frames": int(s.size),
        "mel_integer_zero_count": zero_mel,
        "mel_floor_hit_count": int(np.sum(cand["mel_floored"]))
        if cand["mel_floored"].size else 0,
        "mel_floor_hit_frames": int(np.sum(np.any(cand["mel_floored"], axis=1)))
        if cand["mel_floored"].size else 0,
        "input_quantization_loss": cand["input_quantization_loss"],
        "clipping": cand["clipping"],
        "fft_overflow_frames": cand["fft_overflow_frames"],
        "shift_clamped_frames": cand["shift_clamped_frames"],
        "overflow_counters": cand["overflow_counters"],
    }


# ------------------------------------------------------------------ figures


def write_figures(out_dir: Path, case_id: str, ref: dict,
                  cands: dict) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = []
    names = [c for c in cands if cands[c]["mfcc"].size]
    if not names or "mfcc" not in ref or not ref["mfcc"].size:
        return made

    # Heatmaps: reference, each candidate, and the differences.  The reference
    # and candidate panels share one colour range; the difference panels share
    # a second, symmetric range.  Same frames and axes everywhere.
    ncol = 1 + 2 * len(names)
    fig, axes = plt.subplots(1, ncol, figsize=(4.2 * ncol, 4.6), squeeze=False)
    panels = [("reference float64", ref["mfcc"])]
    panels += [(f"{n}", cands[n]["mfcc"]) for n in names]
    vals = np.concatenate([p[1].ravel() for p in panels])
    vmin, vmax = float(np.min(vals)), float(np.max(vals))
    diffs = [(f"{n} - reference", cands[n]["mfcc"] - ref["mfcc"])
             for n in names]
    dmax = max(float(np.max(np.abs(d[1]))) for d in diffs) or 1e-12

    for ax, (title, data) in zip(axes[0], panels):
        im = ax.imshow(data.T, aspect="auto", origin="lower",
                       vmin=vmin, vmax=vmax, cmap="viridis",
                       extent=[0, data.shape[0], -0.5, data.shape[1] - 0.5])
        ax.set_title(f"{title}\n[{vmin:.2f}, {vmax:.2f}]", fontsize=9)
        ax.set_xlabel("frame")
        ax.set_ylabel("coefficient index")
        fig.colorbar(im, ax=ax, fraction=0.046)
    for ax, (title, data) in zip(axes[0][len(panels):], diffs):
        im = ax.imshow(data.T, aspect="auto", origin="lower",
                       vmin=-dmax, vmax=dmax, cmap="coolwarm",
                       extent=[0, data.shape[0], -0.5, data.shape[1] - 0.5])
        ax.set_title(f"{title}\nsymmetric +-{dmax:.2e}", fontsize=9)
        ax.set_xlabel("frame")
        ax.set_ylabel("coefficient index")
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle(f"{case_id}: MFCC C0..C12, identical frames and axes",
                 fontsize=10)
    fig.tight_layout()
    p = out_dir / f"{case_id}_mfcc_heatmaps.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)

    # per-coefficient error and shift trace
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    width = 0.8 / len(names)
    idx = np.arange(ref["mfcc"].shape[1])
    for j, n in enumerate(names):
        d = np.abs(cands[n]["mfcc"] - ref["mfcc"])
        axes[0].bar(idx + j * width, d.max(axis=0), width, label=f"{n} max")
    axes[0].set_yscale("log")
    axes[0].set_xticks(idx + 0.4 - width / 2)
    axes[0].set_xticklabels([f"C{i}" for i in idx], fontsize=7)
    axes[0].set_ylabel("|error| vs float64")
    axes[0].set_title("per-coefficient maximum absolute error")
    axes[0].legend(fontsize=7)
    for n in names:
        axes[1].step(np.arange(cands[n]["shift_s"].size),
                     cands[n]["shift_s"], where="post", label=n)
    axes[1].set_xlabel("frame")
    axes[1].set_ylabel("BFP exponent s")
    axes[1].set_title("frame exponent per candidate")
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    p = out_dir / f"{case_id}_errors_and_shift.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)
    return made


# ------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--reference-run", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--fw", type=int, default=16,
                    help="Mel weight fraction bits (candidate value)")
    ap.add_argument("--candidates", default="fixed_alpha_half,bfp_pre_quant,"
                                            "q15_then_shift")
    ap.add_argument("--inputs", default="dev,synthetic,extra")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="0 = all frames")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()

    run_id = args.run_id or time.strftime("pilot_%Y%m%d_%H%M%S")
    out_dir = args.out_root / run_id
    # Never overwrite a recorded experiment (review fixed_review_20261004).
    if out_dir.exists():
        print(f"FAIL: output folder already exists: {out_dir}")
        print("      Experiment records are immutable. Use a new --run-id.")
        return 1
    (out_dir / "arrays").mkdir(parents=True, exist_ok=False)
    (out_dir / "figures").mkdir(parents=True, exist_ok=False)
    (out_dir / "source_snapshot").mkdir(parents=True, exist_ok=False)
    for src in sorted((ROOT / "software" / "fixed_model").glob("*.py")):
        shutil.copy2(src, out_dir / "source_snapshot" / f"fixed_model_{src.name}")
    for src in sorted((ROOT / "verification" / "fixed").glob("*.py")):
        shutil.copy2(src, out_dir / "source_snapshot" / f"verification_{src.name}")
    shutil.copy2(Path(__file__).resolve(),
                 out_dir / "source_snapshot" / "run_fixed_pilot.py")
    shutil.copy2(ROOT / "software" / "fixed_model" / "PROVENANCE.json",
                 out_dir / "source_snapshot" / "PROVENANCE.json")
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    ref_run = args.reference_run
    coeff_dir = ref_run / "coefficients" / PROFILE
    if not coeff_dir.is_dir():
        print(f"FAIL: reference coefficients not found: {coeff_dir}")
        return 1

    prof_path = ref_run / "profiles" / f"{PROFILE}.json"
    profile = json.loads(prof_path.read_text(encoding="utf-8"))
    cmeta = json.loads((coeff_dir / "arrays.json").read_text(encoding="utf-8"))
    coeff_hash_check = []
    for name, info in cmeta["arrays"].items():
        fp = coeff_dir / info["file"]
        if not fp.is_file():
            print(f"FAIL: coefficient file missing: {fp}")
            return 1
        digest = sha256_file(fp)
        ok = (digest == info.get("sha256"))
        coeff_hash_check.append({"array": name, "file": info["file"],
                                 "recorded_sha256": info.get("sha256"),
                                 "actual_sha256": digest, "matches": ok})
        if not ok:
            print(f"FAIL: coefficient sha256 mismatch for {fp}")
            return 1

    window = load_bin(coeff_dir / "window.bin", "<f8", (512,))
    mel_float = load_bin(coeff_dir / "mel_filters.bin", "<f8", (26, 257))
    dct_matrix = load_bin(coeff_dir / "dct_matrix.bin", "<f8", (13, 26))

    table = quantize_mel_filterbank(args.fw)
    regen = np.asarray(table.weights_float)
    table_matches_frozen = bool(np.array_equal(regen, mel_float))

    rom = ROOT / "software" / "fixed_model" / "coefficients" / "twiddle_1024_w16.mem"
    twiddle = load_twiddle_rom(str(rom))

    cand_names = [c.strip() for c in args.candidates.split(",") if c.strip()]
    for c in cand_names:
        if c not in CANDIDATES:
            print(f"FAIL: unknown candidate {c!r}")
            return 1
    want = {w.strip() for w in args.inputs.split(",") if w.strip()}
    quant = InputQuantConfig()

    # ---- collect cases (strict: a load failure aborts the run)
    cases: list[dict] = []
    if "dev" in want:
        d = ref_run / "development" / DEV_ID / PROFILE
        rc = load_reference_case(d)
        cases.append({"id": DEV_ID, "group": "development",
                      "source": "frozen_reference_run", "ref": rc,
                      "pcm": ref_run / "development" / DEV_ID / "input_s16le.pcm"})
    if "synthetic" in want:
        sdir = ref_run / "synthetic"
        if not sdir.is_dir():
            print(f"FAIL: synthetic directory not found: {sdir}")
            return 1
        for p in sorted(sdir.iterdir()):
            if not p.is_dir():
                continue
            rc = load_reference_case(p / PROFILE)
            cases.append({"id": p.name, "group": "synthetic",
                          "source": "frozen_reference_run", "ref": rc,
                          "pcm": p / "input_s16le.pcm"})

    extra_manifest = []
    if "extra" in want:
        extras = build_extra_inputs(window, profile["frame_length"],
                                    profile["frame_step"])
        for e in extras:
            pcm = e["pcm"]
            x, y, starts, frames, windowed = float64_frontend(
                pcm, window, profile["frame_length"], profile["frame_step"],
                profile["preemphasis"], profile["pcm_divisor"])
            stages = float64_reference_stages(
                windowed, mel_float, dct_matrix, profile["nfft"],
                float(profile["power_divisor"]))
            pcm_path = out_dir / "arrays" / f"extra_{e['id']}_s16le.pcm"
            pcm.astype("<i2").tofile(pcm_path)
            extra_manifest.append({
                "id": e["id"],
                "note": e["note"],
                "samples": int(pcm.size),
                "frames": int(windowed.shape[0]),
                "pcm_file": pcm_path.name,
                "pcm_sha256": sha256_file(pcm_path),
                "peak_abs_pcm": int(np.max(np.abs(pcm.astype(np.int64))))
                if pcm.size else 0,
                "float64_reference": "generated by scripts/run_fixed_pilot.py "
                                     "(NOT from the frozen reference run)",
                "generator_seed": EXTRA_SEED,
            })
            rc = {"meta": {"arrays": {}}, "hashes": {}, "arrays": {
                "input_float": x, "preemphasis": y, "frame_starts": starts,
                "frames": frames, "windowed": windowed, **stages}}
            cases.append({"id": e["id"], "group": "extra_synthetic",
                          "source": "generated_by_this_script", "ref": rc,
                          "pcm": pcm_path})

    if not cases:
        print("FAIL: no input cases found")
        return 1

    # ---- the input set must be exactly what the task scope allows
    counts = {}
    for c in cases:
        counts[c["group"]] = counts.get(c["group"], 0) + 1
    expected = {}
    if "dev" in want:
        expected["development"] = 1
    if "synthetic" in want:
        expected["synthetic"] = 17
    if "extra" in want:
        expected["extra_synthetic"] = 6
    if counts != expected:
        print(f"FAIL: unexpected input set. got {counts}, expected {expected}")
        return 1
    if "dev" in want:
        dev_frames = int(cases[0]["ref"]["arrays"]["windowed"].shape[0])
        if dev_frames != 534:
            print(f"FAIL: development clip has {dev_frames} frames, expected 534")
            return 1

    # ---- front-end self-check, PART 1: PCM -> pre-emphasis -> framing -> window
    # The old check started at the frozen `windowed` array, so it never
    # exercised pre-emphasis or framing (review fixed_review_20261004).
    frontend_pcm_to_window = []
    for case in cases:
        if case["source"] != "frozen_reference_run":
            continue
        pcm_path = case.get("pcm")
        if pcm_path is None or not Path(pcm_path).is_file():
            print(f"FAIL: input PCM missing for {case['id']}: {pcm_path}")
            return 1
        pcm = np.fromfile(pcm_path, dtype="<i2")
        x, y, starts, frames, windowed = float64_frontend(
            pcm, window, profile["frame_length"], profile["frame_step"],
            profile["preemphasis"], profile["pcm_divisor"])
        arrs = case["ref"]["arrays"]
        row = {"id": case["id"], "pcm_samples": int(pcm.size),
               "pcm_sha256": sha256_file(Path(pcm_path)),
               "frames": int(windowed.shape[0])}
        mine_fe = {"input_float": x, "preemphasis": y,
                   "frame_starts": starts, "frames": frames,
                   "windowed": windowed}
        for name, mine_a in mine_fe.items():
            ref_a = arrs.get(name)
            if ref_a is None:
                print(f"FAIL: {case['id']} reference lacks {name}")
                return 1
            if ref_a.shape != mine_a.shape:
                print(f"FAIL: {case['id']} {name} shape {mine_a.shape} vs "
                      f"reference {ref_a.shape}")
                return 1
            if name == "frame_starts":
                row[name] = int(np.max(np.abs(ref_a - mine_a)))                     if ref_a.size else 0
            else:
                row[name] = float(np.max(np.abs(ref_a - mine_a)))                     if ref_a.size else 0.0
        frontend_pcm_to_window.append(row)

    # ---- front-end self-check, PART 2: FFT onwards, from the frozen window
    frontend_fft_onwards = []
    for case in cases:
        if case["source"] != "frozen_reference_run":
            continue
        arrs = case["ref"]["arrays"]
        mine = float64_reference_stages(
            arrs["windowed"], mel_float, dct_matrix, profile["nfft"],
            float(profile["power_divisor"]))
        row = {"id": case["id"], "frames": int(arrs["windowed"].shape[0])}
        for name in ("fft", "power", "mel_energies", "log_mel", "mfcc"):
            if name in arrs and arrs[name].shape == mine[name].shape:
                row[name] = err_stats(arrs[name], mine[name])["max_abs"]
        frontend_fft_onwards.append(row)

    # ---- run candidates
    results = []
    t0 = time.time()
    for case in cases:
        arrs = case["ref"]["arrays"]
        if "windowed" not in arrs:
            continue
        windowed = arrs["windowed"]
        if args.max_frames and windowed.shape[0] > args.max_frames:
            windowed = windowed[:args.max_frames]
            arrs = {k: (v[:args.max_frames] if getattr(v, "ndim", 0) >= 1
                        and v.shape and v.shape[0] >= windowed.shape[0]
                        else v) for k, v in arrs.items()}
        ref_stage = {k: arrs[k] for k in
                     ("fft", "power", "mel_energies", "log_mel", "mfcc")
                     if k in arrs}
        # reference fft is stored as 257 one-sided bins already
        cands = {}
        for cname in cand_names:
            cands[cname] = run_candidate(windowed, cname, table, twiddle,
                                         dct_matrix, quant)
        entry = {
            "id": case["id"],
            "group": case["group"],
            "reference_source": case["source"],
            "frames": int(windowed.shape[0]),
            "windowed_peak_abs": float(np.max(np.abs(windowed)))
            if windowed.size else 0.0,
            "candidates": {c: summarize(ref_stage, cands[c], table)
                           for c in cand_names},
        }
        figs = []
        if not args.no_figures and case["id"] in (DEV_ID, "bfp_octave_steps",
                                                  "fullscale_alternating"):
            figs = write_figures(out_dir / "figures", case["id"],
                                 ref_stage, cands)
        entry["figures"] = figs

        # dump the integer arrays for the dev case so RTL work can reuse them
        if case["id"] == DEV_ID:
            for cname in cand_names:
                c = cands[cname]
                np.asarray(c["psum"], dtype=np.int64).tofile(
                    out_dir / "arrays" / f"{DEV_ID}_{cname}_psum_i64.bin")
                np.asarray([[int(t) for t in row] for row in c["mel_int"]],
                           dtype=object).astype("int64").tofile(
                    out_dir / "arrays" / f"{DEV_ID}_{cname}_mel_i64.bin")
                np.asarray(c["shift_s"], dtype=np.int64).tofile(
                    out_dir / "arrays" / f"{DEV_ID}_{cname}_shift_i64.bin")
                np.asarray(c["mfcc"], dtype="<f8").tofile(
                    out_dir / "arrays" / f"{DEV_ID}_{cname}_mfcc_f64.bin")
        results.append(entry)
        print(f"  [{case['group']:17s}] {case['id']:28s} "
              f"frames={entry['frames']:4d} " +
              " ".join(f"{c}:mfcc_max={entry['candidates'][c]['stages'].get('mfcc', {}).get('max_abs', float('nan')):.3e}"
                       for c in cand_names))
    elapsed = time.time() - t0

    # ---- coefficient-only sweep on the development power spectrum
    coef_sweep = []
    devref = next((c for c in cases if c["id"] == DEV_ID), None)
    if devref and "power" in devref["ref"]["arrays"]:
        coef_sweep = coefficient_error_sweep(
            devref["ref"]["arrays"]["power"][:128])

    # ---- write outputs
    csv_path = out_dir / "stage_errors.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["input_id", "group", "reference_source", "candidate",
                    "frames", "stage", "max_abs", "rmse",
                    "max_rel_where_ref_nonzero"])
        for e in results:
            for cname, s in e["candidates"].items():
                for stage, st in s["stages"].items():
                    w.writerow([e["id"], e["group"], e["reference_source"],
                                cname, e["frames"], stage, st["max_abs"],
                                st["rmse"], st["max_rel_where_ref_nonzero"]])

    coef_csv = out_dir / "mfcc_coefficient_errors.csv"
    with open(coef_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["input_id", "group", "candidate", "coefficient",
                    "max_abs", "rmse", "frame_of_max"])
        for e in results:
            for cname, s in e["candidates"].items():
                for pc in s["per_coefficient"]:
                    w.writerow([e["id"], e["group"], cname, pc["coefficient"],
                                pc["max_abs"], pc["rmse"], pc["frame_of_max"]])

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(elapsed, 1),
        "experiment_class": "mixed_precision",
        "experiment_class_note":
            "float64 pre-emphasis/framing/window and float64 log/DCT; only the "
            "FFT input quantization, the FFT and Power/Mel are integer. This is "
            "NOT a completed fixed-point MFCC model (Q2 remains).",
        "command": " ".join([Path(sys.argv[0]).name] + sys.argv[1:]),
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "platform": platform.platform(),
            "executable": sys.executable,
            "thread_env": {k: os.environ.get(k) for k in
                           ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                            "MKL_NUM_THREADS")},
        },
        "profile": {
            "path": str(prof_path),
            "sha256": sha256_file(prof_path),
            "profile_id": profile.get("profile_id"),
            "spec_id": profile.get("spec_id"),
        },
        "reference_run": str(ref_run),
        "reference_coefficients": {
            "dir": str(coeff_dir),
            "arrays_json_sha256": sha256_file(coeff_dir / "arrays.json"),
            "verified": coeff_hash_check,
        },
        "reference_array_hash_verification": {
            "policy": "every referenced .bin is hashed and compared with the "
                      "sha256 recorded in arrays.json; a mismatch aborts",
            "per_case": {c["id"]: c["ref"].get("hashes", {}) for c in cases},
        },
        "bfp_shift_rule": SHIFT_RULE_DEFAULT,
        "fixed_model_sources": {
            p.name: sha256_file(p) for p in
            sorted((ROOT / "software" / "fixed_model").glob("*.py"))
        },
        "fixed_model_rom": {
            "file": str(rom), "sha256": sha256_file(rom),
        },
        "verification_sources": {
            p.name: sha256_file(p) for p in
            sorted((ROOT / "verification" / "fixed").glob("*.py"))
        },
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "mel_table": table.manifest(),
        "mel_table_matches_frozen_float_table": table_matches_frozen,
        "input_quantization": {
            **{k: v for k, v in asdict(quant).items()},
            "note": "one quantization of u * 2**(s-1); the clamp removes the "
                    "asymmetric minimum code -32768",
        },
        "log_floor": LOG_FLOOR,
        "log_floor_note": "applied to the exponent-corrected energy "
                          "T * 2**(mel_exp_log2); a positive integer T can be "
                          "below the floor when s is large",
        "candidates": {c: {**{k: v for k, v in asdict(CANDIDATES[c]).items()}}
                       for c in cand_names},
        "extra_synthetic_inputs": extra_manifest,
        "frontend_selfcheck_pcm_to_window_max_abs": frontend_pcm_to_window,
        "frontend_selfcheck_fft_onwards_max_abs": frontend_fft_onwards,
        "mel_coefficient_only_sweep": coef_sweep,
        "results": results,
        "not_done": [
            "log and DCT are float64; no integer log/DCT (Q2)",
            "pre-emphasis, framing and window are float64 (Q2)",
            "no RTL was written, simulated, synthesised or run on a board",
            "evaluation speech (20 clips) deliberately not opened",
            "fixed-point acceptance tolerances are not established; the "
            "numbers here are measurements plus a proposal",
        ],
    }
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print()
    print(f"run_id            : {run_id}")
    print(f"bfp shift rule    : {SHIFT_RULE_DEFAULT}")
    print(f"output            : {out_dir}")
    print(f"experiment class  : mixed_precision (NOT a complete fixed model)")
    print(f"cases             : {len(results)}")
    print(f"candidates        : {', '.join(cand_names)}")
    print(f"mel Fw            : {args.fw} (candidate), table matches frozen "
          f"float table: {table_matches_frozen}")
    print(f"elapsed           : {elapsed:.1f} s")
    print("RESULT: COMPLETED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
