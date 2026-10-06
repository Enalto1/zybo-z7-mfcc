#!/usr/bin/env python3
"""Re-verify the reused FFT bit model inside this repository.

docs/NEXT_TASK_CLAUDE_FIXED_POINT.md section 3 requires that a reused FFT bit
model carry its origin, hash, supported sizes and existing round/wrap
behaviour.  PROVENANCE.json records those; this script checks that the copy in
the repository still behaves as recorded, using only repository files:

  1. the twiddle ROM is independently recomputed from cos/sin(-2*pi*a/1024);
  2. deterministic inputs give hand-derived outputs (impulse, DC, tone);
  3. the output matches an ideal float64 FFT/N to within a couple of LSB;
  4. wrap (not saturate) is still the overflow behaviour;
  5. the recorded file hashes still match.

Run:  python verification/fixed/test_fft_bitmodel.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from software.fixed_model.fft_bitmodel import (  # noqa: E402
    bit_reverse, fft_fixed, fft_fixed_natural, load_twiddle_rom)

MODEL = ROOT / "software" / "fixed_model" / "fft_bitmodel.py"
ROM = ROOT / "software" / "fixed_model" / "coefficients" / "twiddle_1024_w16.mem"
PROV = ROOT / "software" / "fixed_model" / "PROVENANCE.json"

FAILS: list[str] = []
CHECKS = 0


def eq(label, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{label}: got {got!r}, expected {want!r}")


def le(label, got, limit):
    global CHECKS
    CHECKS += 1
    if not (got <= limit):
        FAILS.append(f"{label}: got {got!r}, expected <= {limit!r}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------- recorded hashes
prov = json.loads(PROV.read_text(encoding="utf-8"))
recorded = {e["file"]: e["sha256"] for e in prov["reused"]}
eq("fft_bitmodel.py hash matches PROVENANCE",
   sha(MODEL), recorded["fft_bitmodel.py"])
eq("twiddle ROM hash matches PROVENANCE",
   sha(ROM), recorded["coefficients/twiddle_1024_w16.mem"])

# --------------------------------------------------- ROM independent recompute
tw_re, tw_im = load_twiddle_rom(str(ROM))


def q15_ties_even_saturating(values):
    scaled = np.asarray(values, dtype=np.float64) * 32768.0
    nearest = np.round(scaled)                      # numpy round is ties-to-even
    return np.clip(nearest, -32768, 32767).astype(np.int64)


a = np.arange(1024)
ang = -2.0 * np.pi * a / 1024.0
eq("ROM real recompute", int(np.sum(q15_ties_even_saturating(np.cos(ang)) != tw_re)), 0)
eq("ROM imag recompute", int(np.sum(q15_ties_even_saturating(np.sin(ang)) != tw_im)), 0)
eq("ROM[0] is +1 saturated", (int(tw_re[0]), int(tw_im[0])), (32767, 0))
eq("ROM[256] is -j", (int(tw_re[256]), int(tw_im[256])), (0, -32768))

# --------------------------------------------------- deterministic outputs
N = 512

# impulse of 32767 -> every natural-order bin equals round(32767/512) = 64
imp = [0] * N
imp[0] = 32767
re_n, im_n, ovf = fft_fixed_natural(imp, [0] * N, tw_re, tw_im)
eq("impulse all bins 64", set(re_n), {64})
eq("impulse imag all zero", set(im_n), {0})
eq("impulse no overflow", ovf, False)

# half-scale DC -> bin0 = 16384, everything else exactly zero
dc = [16384] * N
re_n, im_n, ovf = fft_fixed_natural(dc, [0] * N, tw_re, tw_im)
eq("DC bin0", re_n[0], 16384)
eq("DC other bins zero", set(re_n[1:]) | set(im_n), {0})
eq("DC no overflow", ovf, False)

# integer-bin cosine, amplitude 0.9 -> bins 40 and 472 only
k = 40
tone = q15_ties_even_saturating(
    0.9 * np.cos(2 * np.pi * k * np.arange(N) / N)).tolist()
re_n, im_n, ovf = fft_fixed_natural(tone, [0] * N, tw_re, tw_im)
nz = [i for i in range(N) if re_n[i] or im_n[i]]
eq("tone occupies two bins", nz, [40, 472])
eq("tone conjugate symmetry", re_n[40], re_n[472])
le("tone magnitude near 0.45 full scale", abs(re_n[40] - 14746), 4)
eq("tone no overflow", ovf, False)

# bit-reversed vs natural ordering must agree through bit_reverse()
br_re, br_im, _ = fft_fixed(tone, [0] * N, tw_re, tw_im)
mapped_ok = all(br_re[o] == re_n[bit_reverse(o, 9)] and
                br_im[o] == im_n[bit_reverse(o, 9)] for o in range(N))
eq("core order maps to natural order", mapped_ok, True)

# --------------------------------------------------- vs ideal float64 FFT/N
rng = np.random.default_rng(20261004)
worst = 0.0
for _ in range(8):
    x = q15_ties_even_saturating(rng.normal(0.0, 0.1, N))
    re_n, im_n, ovf = fft_fixed_natural(x.tolist(), [0] * N, tw_re, tw_im)
    got = (np.asarray(re_n) + 1j * np.asarray(im_n)) / 32768.0
    ref = np.fft.fft(x / 32768.0) / N
    worst = max(worst, float(np.max(np.abs(got - ref)) * 32768.0))
    eq("random frame no overflow", ovf, False)
le("worst error vs ideal FFT/N (LSB)", worst, 2.0)

# --------------------------------------------------- wrap, not saturate
# A full-scale bipolar sign pattern that reaches the Type-II +2**17 boundary.
# Behaviour must be wrap: the reported overflow is accompanied by a result
# that is NOT clamped to the representable maximum.
found = False
rng2 = np.random.default_rng(7)
for _ in range(400):
    cand = np.where(rng2.random(N) < 0.5, -32768, 32767).astype(np.int64)
    re_n, im_n, ovf = fft_fixed_natural(cand.tolist(), [0] * N, tw_re, tw_im)
    if ovf:
        found = True
        ref = np.fft.fft(cand / 32768.0) / N
        got = (np.asarray(re_n) + 1j * np.asarray(im_n)) / 32768.0
        err = float(np.max(np.abs(got - ref)) * 32768.0)
        # a saturating core would stay within ~1 LSB of the clamped value;
        # a wrapping core produces a large signed error
        le("wrap produces a large error, not a clamp", 100.0, err)
        break
eq("found an overflowing full-scale real frame", found, True)
# the same frame with one bit of headroom must be clean
re_n, im_n, ovf = fft_fixed_natural((cand // 2).tolist(), [0] * N, tw_re, tw_im)
eq("one bit of headroom clears it", ovf, False)

# --------------------------------------------------- supported sizes
for log2n in (3, 4, 5, 6, 7, 8, 9, 10):
    n = 1 << log2n
    x = q15_ties_even_saturating(rng.normal(0.0, 0.05, n)).tolist()
    re_n, im_n, ovf = fft_fixed_natural(x, [0] * n, tw_re, tw_im)
    got = (np.asarray(re_n) + 1j * np.asarray(im_n)) / 32768.0
    ref = np.fft.fft(np.asarray(x) / 32768.0) / n
    le(f"N={n} error vs ideal (LSB)",
       float(np.max(np.abs(got - ref)) * 32768.0), 2.5)

print(f"test_fft_bitmodel: {CHECKS} checks, {len(FAILS)} failures")
for f in FAILS:
    print("  FAIL", f)
print("RESULT:", "PASS" if not FAILS else "FAIL")
sys.exit(0 if not FAILS else 1)
