#!/usr/bin/env python3
"""Independent gain, output-grid, floor and serialization regressions.

The FFT oracle uses closed-form spectra for integer-code impulse/DC/bin tones;
it never calls the runner's restoration helpers or NumPy FFT.  The legacy
mutation changes actual B/C computations in an in-memory copy of run_ablation.
No source or frozen reference files are modified, and no speech is opened.

Run normally for PASS, or use --inject-legacy-scale-bug B/C/both to demonstrate
that the original /512 amplitude error causes a nonzero exit status.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import math
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import run_fft_precision_study as study  # noqa: E402
from software.fixed_model.coeffs import quantize_mel_filterbank  # noqa: E402
from software.fixed_model.fft_bitmodel import load_twiddle_rom  # noqa: E402
from software.fixed_model.pipeline import (  # noqa: E402
    InputQuantConfig, quantize_frame_input)

CHECKS = 0
FAILS = []
N = 512
BINS = 257
F = 15
FW = 16
B_PATH = "B_input_quant_float_fft"
C_PATH = "C_B_plus_output_rounding"


def check(label, fn, *args, **kwargs):
    global CHECKS
    CHECKS += 1
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        FAILS.append(f"{label}: {type(exc).__name__}: {exc}")


def equal(got, want):
    if got != want:
        raise AssertionError(f"got {got!r}; expected {want!r}")


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def close(got, want):
    # No absolute tolerance: even the small s=24 probes must expose a
    # factor-512 error, and analytically zero bins must stay exactly zero.
    np.testing.assert_allclose(got, want, rtol=3e-15, atol=0)


def make_dct():
    return np.asarray([
        [math.sqrt((1 if c == 0 else 2) / 26)
         * math.cos(math.pi * c * (m + 0.5) / 26) for m in range(26)]
        for c in range(13)], dtype=float)


def integer_probes():
    """Return q and its exact Gaussian-integer DFT, not a numerical FFT."""
    probes = []
    quadrature = np.asarray([1, -1j, -1, 1j] * 65)[:BINS]
    for sign in (1, -1):
        for amplitude in (256, 768, 1024):
            q = np.zeros(N, dtype=np.int64)
            q[0] = sign * amplitude
            probes.append((f"impulse0_{sign * amplitude}", q,
                           np.full(BINS, sign * amplitude, complex)))
            q = np.zeros(N, dtype=np.int64)
            q[N // 4] = sign * amplitude
            probes.append((f"impulse128_{sign * amplitude}", q,
                           sign * amplitude * quadrature))

        amplitude = sign * 2048
        q = np.full(N, amplitude, dtype=np.int64)
        spec = np.zeros(BINS, complex)
        spec[0] = N * amplitude
        probes.append((f"dc_{sign}", q, spec))
        q = np.tile([amplitude, 0, -amplitude, 0], N // 4)
        spec = np.zeros(BINS, complex)
        spec[128] = N // 2 * amplitude
        probes.append((f"cos_bin128_{sign}", q, spec))
        q = np.tile([0, amplitude, 0, -amplitude], N // 4)
        spec = np.zeros(BINS, complex)
        spec[128] = -1j * (N // 2) * amplitude
        probes.append((f"sin_bin128_{sign}", q, spec))
        q = np.tile([amplitude, -amplitude], N // 2)
        spec = np.zeros(BINS, complex)
        spec[256] = N * amplitude
        probes.append((f"nyquist_{sign}", q, spec))

        # k=1 has +0.5 - 1.5j grid components (both signs are tested), so
        # real and imaginary ties must round separately, towards even.
        q = np.zeros(N, dtype=np.int64)
        q[0], q[128] = sign * 256, sign * 768
        probes.append((f"mixed_ties_{sign}", q,
                       sign * (256 + 768 * quadrature)))
    return probes


def output_rounded_spectrum(spec):
    # Fraction.__round__ is an independent exact rational ties-even oracle.
    # Multiplying by 512 returns to unnormalised integer-input DFT units.
    return np.asarray([
        complex(round(Fraction(int(v.real), N)),
                round(Fraction(int(v.imag), N))) * N for v in spec])


def reference_from_spectra(spectra, table, dct):
    power = (spectra.real ** 2 + spectra.imag ** 2) / N
    mel = power @ np.asarray(table.weights_float).T
    logs = np.log(np.maximum(mel, 1e-12))
    return {"fft": spectra, "power": power, "mel_energies": mel,
            "log_mel": logs, "mfcc": logs @ dct.T}


def legacy_mutant(paths):
    """Inject the old gain bug before power/Mel, into actual execution code."""
    tree = ast.parse(inspect.getsource(study.run_ablation))
    names = {"fb" if p == "B" else "fc" for p in paths}
    changed = []

    class DivideAmplitude(ast.NodeTransformer):
        def visit_Assign(self, node):
            self.generic_visit(node)
            if any(isinstance(t, ast.Name) and t.id in names
                   for t in node.targets):
                node.value = ast.BinOp(left=node.value, op=ast.Div(),
                                       right=ast.Constant(N))
                changed.extend(t.id for t in node.targets if isinstance(t, ast.Name))
            return node

    tree = DivideAmplitude().visit(tree)
    equal(sorted(changed), sorted(names))
    ast.fix_missing_locations(tree)
    namespace = dict(vars(study))
    exec(compile(tree, "<legacy-scale-mutant>", "exec"), namespace)
    return namespace["run_ablation"]


def test_ablation(table, dct, twiddle, injected):
    quant = InputQuantConfig()
    weights = np.asarray(table.weights_int, dtype=np.int64)
    frames, shifts, names, expected_b, expected_c = [], [], [], [], []
    for s in (-2, 0, 1, 6, 24):
        for name, q, spec in integer_probes():
            unit = math.ldexp(1.0, 1 - F - s)
            frame = q.astype(float) * unit
            recovered, clipping = quantize_frame_input(frame.tolist(), s, quant)
            check(f"input integer codes {name} s={s}",
                  np.testing.assert_array_equal, recovered, q)
            check(f"input no clipping {name} s={s}", equal,
                  clipping.clipped_frame_samples, 0)
            frames.append(frame)
            shifts.append(s)
            names.append(f"{name} s={s}")
            expected_b.append(spec * unit)
            expected_c.append(output_rounded_spectrum(spec) * unit)
    frames = np.asarray(frames)
    expected_b, expected_c = np.asarray(expected_b), np.asarray(expected_c)
    ref = reference_from_spectra(expected_b, table, dct)
    args = (frames, ref, np.asarray(shifts), quant, weights, FW, dct, twiddle)
    runner = legacy_mutant(injected) if injected else study.run_ablation
    result = runner(*args, table=table)
    for path, expected in ((B_PATH, expected_b), (C_PATH, expected_c)):
        arrays = result[path]["_arrays"]
        expected_power = (expected.real ** 2 + expected.imag ** 2) / N
        expected_mel = expected_power @ (weights.astype(float) / 65536).T
        for i, name in enumerate(names):
            check(f"{path} FFT {name}", close, arrays["fft"][i], expected[i])
            check(f"{path} Power /512 {name}", close,
                  arrays["power"][i], expected_power[i])
        check(f"{path} Mel common unit", close,
              arrays["mel_energies"], expected_mel)
        check(f"{path} 13 coefficient errors", equal,
              len(result[path]["per_coefficient"]), 13)

    # This is a mutation of the real runner, not a comparison between two
    # handwritten gain formulas.  Keep the tested input nonzero after C round.
    index = names.index("impulse0_1024 s=0")
    one_ref = {k: v[index:index + 1] for k, v in ref.items()}
    for path_id, path, oracle in (("B", B_PATH, expected_b),
                                  ("C", C_PATH, expected_c)):
        mutant = legacy_mutant(path_id)
        mutated = mutant(frames[index:index + 1], one_ref,
                         np.asarray([shifts[index]]), quant, weights, FW,
                         dct, twiddle, table=table)[path]["_arrays"]
        check(f"legacy {path_id} /512 FFT mutant rejected", raises,
              AssertionError, close, mutated["fft"], oracle[index:index + 1])
        expected_power = np.abs(oracle[index:index + 1]) ** 2 / N
        check(f"legacy {path_id} /512 squared Power mutant rejected", raises,
              AssertionError, close, mutated["power"], expected_power)
        check(f"legacy {path_id} mutation really divided Power by 512^2", close,
              mutated["power"] * N ** 2, expected_power)


def test_width_scale(table, dct, twiddle):
    probes = [(name, q, spec) for name, q, spec in integer_probes()
              if name in {"impulse0_1024", "dc_1", "dc_-1",
                          "cos_bin128_1", "sin_bin128_1"}]
    # These inputs are exactly representable.  DC/tone *outputs* can still
    # differ from ideal because +1 in the fixed twiddle ROM is 32767/32768;
    # wider data paths retain that coefficient error instead of hiding it.
    frames = np.asarray([q / 8192 for _, q, _ in probes])
    expected_fft = np.asarray([spec / 8192 for _, _, spec in probes])
    ref = reference_from_spectra(expected_fft, table, dct)
    weights = np.asarray(table.weights_int, dtype=np.int64)
    expected_mel = ref["power"] @ (weights.astype(float) / 65536).T
    impulse_index = [name for name, _, _ in probes].index("impulse0_1024")
    for target in (0.5, 0.975):
        for key, (_, _, cfg) in study.WIDTH_CONFIGS.items():
            result = study.run_width(frames, ref, target, key, dct, twiddle,
                                     weights, FW, table=table)
            label = f"{key} target={target}"
            raw = result["_integer_arrays"]
            restored_fft, restored_power, restored_mel = [], [], []
            for row_index, shift_row in enumerate(raw["shift"]):
                s = int(shift_row[0])
                re, im = raw["fft_re"][row_index], raw["fft_im"][row_index]
                # Derive units from physical DFT normalization /512.  Never
                # consult total_shift or any production restoration helper.
                scale = 512 * math.ldexp(1.0, -cfg.out_f) * math.ldexp(1.0, 1 - s)
                restored_fft.append([(r + 1j * j) * scale for r, j in zip(re, im)])
                psum = [int(r) ** 2 + int(j) ** 2 for r, j in zip(re, im)]
                restored_power.append([v * scale ** 2 / 512 for v in psum])
                acc = [sum(p * int(w) for p, w in zip(psum, band)) for band in weights]
                restored_mel.append([v * scale ** 2 / 512 / 65536 for v in acc])
            for stage, expected in (("fft", restored_fft), ("power", restored_power),
                                    ("mel_energies", restored_mel)):
                check(f"{label} {stage} units from raw integers", close,
                      result["_arrays"][stage], expected)
            for stage, expected in (("fft", expected_fft), ("power", ref["power"]),
                                    ("mel_energies", expected_mel)):
                check(f"{label} impulse analytic {stage}", close,
                      result["_arrays"][stage][impulse_index], expected[impulse_index])
            check(f"{label} physical normalization S", equal,
                  result["physical_fft_shift_S"], 9)
            check(f"{label} output code requantization", equal,
                  result["output_requant_shift_bits"], cfg.data_frac - cfg.out_f)
            check(f"{label} total integer code shift", equal,
                  result["integer_code_shift_bits"], 9 + cfg.data_frac - cfg.out_f)
            check(f"{label} Power exponent", equal,
                  result["power_exponent_at_s0"], 11 - 2 * cfg.out_f)
            check(f"{label} Mel exponent", equal,
                  result["mel_exponent_at_s0"], 11 - 2 * cfg.out_f - FW)
            check(f"{label} no FFT overflow", equal,
                  result["fft_overflow_frames"], 0)
            check(f"{label} no input clipping", equal,
                  result["clipping"]["clipped_frame_samples"], 0)


def test_storage_and_floor():
    with tempfile.TemporaryDirectory(prefix="mfcc_precision_test_") as directory:
        directory = Path(directory)
        for label, rows, binary in (
                ("int64 endpoints", [[-(1 << 63), (1 << 63) - 1, 0]], True),
                ("positive int64 overflow", [[0, 1 << 63, 1 << 90]], False),
                ("negative int64 overflow", [[-(1 << 63) - 1, -(1 << 91)]], False)):
            path = directory / (label.replace(" ", "_") + ".bin")
            meta = study.save_int_array(path, rows)
            saved = directory / meta["file"]
            if binary:
                restored = np.fromfile(saved, dtype="<i8").tolist()
            else:
                restored = [int(v) for v in saved.read_text(encoding="utf-8").split()]
                check(f"{label} no truncated binary", equal, path.exists(), False)
            check(f"{label} exact roundtrip", equal,
                  restored, [int(v) for row in rows for v in row])
            check(f"{label} exact minimum", equal, meta["min"], min(restored))
            check(f"{label} exact maximum", equal, meta["max"], max(restored))

    for invalid in (-1.0, -np.nextafter(0.0, 1.0), -np.inf, np.inf, np.nan):
        check(f"invalid Mel {invalid!r} rejected before floor", raises,
              ValueError, study.logs_and_mfcc,
              np.asarray([[invalid]]), np.asarray([[1.0]]))
    floor = 1e-12
    energies = np.asarray([[0, -0.0, np.nextafter(floor, 0.0), floor,
                            np.nextafter(floor, np.inf), 2 * floor]])
    log, mfcc = study.logs_and_mfcc(energies, np.eye(energies.shape[1]))
    check("floor below/at/above boundary", np.testing.assert_array_equal,
          log, np.asarray([[math.log(max(v, floor)) for v in energies[0]]]))
    check("floor DCT identity", np.testing.assert_array_equal, mfcc, log)
    check("only strictly above-to-below counts as floor regression", equal,
          study.floor_regression(
              np.asarray([0, floor, 2 * floor, 2 * floor, 2 * floor]),
              np.asarray([0, 0, 0, floor, 3 * floor])), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inject-legacy-scale-bug", choices=("B", "C", "both"))
    args = parser.parse_args()
    injected = "BC" if args.inject_legacy_scale_bug == "both" else args.inject_legacy_scale_bug
    table = quantize_mel_filterbank(FW)
    dct = make_dct()
    twiddle = load_twiddle_rom(str(ROOT / "software/fixed_model/coefficients/twiddle_1024_w16.mem"))
    test_ablation(table, dct, twiddle, injected)
    test_width_scale(table, dct, twiddle)
    test_storage_and_floor()
    print(f"test_fft_precision_study: {CHECKS} checks, {len(FAILS)} failures")
    for failure in FAILS[:8]:
        print("  FAIL", failure)
    if len(FAILS) > 8:
        print(f"  ... {len(FAILS) - 8} additional failures")
    print("RESULT:", "FAIL" if FAILS else "PASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
