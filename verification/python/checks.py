"""Validation companions for the explicit MFCC reference pipeline.

The package adapter calls unmodified python_speech_features APIs. It is a
cross-check with shared NumPy/SciPy dependencies, not an independent oracle.
Selected scalar DFT/DCT sums and signal properties provide additional checks.
No audio files are read or changed by this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from python_speech_features import base as psf_base
from python_speech_features import sigproc
from scipy.fftpack import dct


STAGES = (
    "input_float", "preemphasis", "frame_starts", "frames", "windowed",
    "fft", "power", "mel_energies", "log_mel", "dct", "frame_energy", "mfcc",
)
SYNTHETIC_SEED = 20261004
EXPECTED_MEL_EDGES = (
    0, 2, 4, 7, 10, 13, 16, 20, 24, 29, 34, 40, 46, 53,
    60, 68, 77, 87, 97, 109, 122, 136, 152, 169, 188, 209, 231, 256,
)


def synthetic_inputs() -> dict[str, np.ndarray]:
    """Return deterministic PCM16 probes; sinusoid quantization is ties-to-even.

    PCG64 is named explicitly rather than relying on default_rng's default.
    Callers should hash the returned little-endian int16 bytes in their manifest.
    """
    probes = {
        f"boundary_{length}": np.zeros(length, dtype=np.int16)
        for length in (0, 511, 512, 671, 672, 832)
    }
    length = 832
    sample = np.arange(length, dtype=np.float64)
    probes["silence"] = np.zeros(length, dtype=np.int16)
    for index in (0, 511):
        impulse = np.zeros(length, dtype=np.int16)
        impulse[index] = 16384
        probes[f"impulse_n{index}"] = impulse
    probes["dc_positive_8192"] = np.full(length, 8192, dtype=np.int16)
    probes["dc_negative_8192"] = np.full(length, -8192, dtype=np.int16)
    for name, frequency in (("tone_bin32_1000hz", 1000.0),
                            ("tone_noninteger_1037hz", 1037.0)):
        probes[name] = np.rint(12000.0 * np.sin(2.0 * np.pi * frequency * sample / 16000.0)).astype(np.int16)
    composite = (7000.0 * np.sin(2.0 * np.pi * 500.0 * sample / 16000.0)
                 + 5000.0 * np.sin(2.0 * np.pi * 2237.0 * sample / 16000.0))
    probes["composite_500_2237hz"] = np.rint(composite).astype(np.int16)
    probes["small_alternating_1"] = np.resize(np.array([1, -1], dtype=np.int16), length)
    probes["fullscale_alternating"] = np.resize(np.array([32767, -32768], dtype=np.int16), length)
    generator = np.random.Generator(np.random.PCG64(SYNTHETIC_SEED))
    probes["noise_pcg64_seed20261004"] = generator.integers(-32768, 32768, length, dtype=np.int16)
    return probes


def _metric(actual: Any, expected: Any, atol: float, rtol: float) -> dict[str, Any]:
    a, e = np.asarray(actual), np.asarray(expected)
    shape_ok = a.shape == e.shape
    finite_actual = bool(np.all(np.isfinite(a)))
    finite_expected = bool(np.all(np.isfinite(e)))
    record: dict[str, Any] = {
        "actual_shape": list(a.shape), "expected_shape": list(e.shape),
        "shape_ok": shape_ok, "finite_actual": finite_actual,
        "finite_expected": finite_expected, "finite": finite_actual and finite_expected,
        "elements": int(e.size), "atol": float(atol), "rtol": float(rtol),
        "max_abs": None, "rmse": None, "violations": None, "passed": False,
    }
    if not shape_ok or not finite_actual or not finite_expected:
        return record
    # A complex-bin error is its complex magnitude, not just its real part.
    delta = np.abs(a - e)
    limit = atol + rtol * np.abs(e)
    violations = int(np.count_nonzero(delta > limit))
    record.update(
        max_abs=float(np.max(delta)) if delta.size else 0.0,
        rmse=float(np.sqrt(np.mean(np.square(delta)))) if delta.size else 0.0,
        violations=violations,
        passed=violations == 0,
    )
    return record


def compare_stages(actual: Mapping[str, Any], expected: Mapping[str, Any],
                   atol: float, rtol: float) -> dict[str, dict[str, Any]]:
    """Check stage shape/finiteness before abs(error) <= atol + rtol*abs(ref).

    Frame starts are an indexing contract and must match exactly. Missing stages
    fail explicitly instead of disappearing from an intersection of key sets.
    """
    if not (math.isfinite(atol) and math.isfinite(rtol) and atol >= 0 and rtol >= 0):
        raise ValueError("atol and rtol must be finite and nonnegative")
    records = {}
    for stage in STAGES:
        if stage not in actual or stage not in expected:
            records[stage] = {"passed": False, "error": "missing stage",
                              "missing_actual": stage not in actual,
                              "missing_expected": stage not in expected}
        else:
            absolute, relative = (0.0, 0.0) if stage == "frame_starts" else (atol, rtol)
            records[stage] = _metric(actual[stage], expected[stage], absolute, relative)
    return records


def _frame_count(length: int, profile: Mapping[str, Any]) -> int:
    frame, step = int(profile["frame_length"]), int(profile["frame_step"])
    if profile["frame_policy"] == "full_frames_only":
        return 0 if length < frame else 1 + (length - frame) // step
    if profile["frame_policy"] == "ceil_zero_pad":
        if length == 0:
            raise ValueError("The package/native profile does not accept empty PCM")
        return 1 if length <= frame else 1 + (length - frame + step - 1) // step
    raise ValueError(f"Unsupported frame policy: {profile['frame_policy']}")


def _log_input(energy: np.ndarray, profile: Mapping[str, Any]) -> np.ndarray:
    policy = profile["log_policy"]
    if policy in ("floor", "floor_clamp", "clamp_min"):
        return np.maximum(energy, float(profile["log_floor"]))
    if policy in ("zero_to_epsilon", "replace_exact_zero", "zero_replace", "replace_zero_only"):
        return np.where(energy == 0.0, float(profile["log_floor"]), energy)
    raise ValueError(f"Unsupported log policy: {policy}")


def package_reference(pcm: np.ndarray, profile: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """Stage-visible package comparator with explicit frame/log adaptations.

    `full_frames_only` selects the complete frames from sigproc.framesig; no
    signal is trimmed before pre-emphasis. Log floor is applied outside the
    package. Raw Mel energy is formed from the package's filters before its
    customary exact-zero replacement, allowing that policy to remain visible.
    """
    if not isinstance(pcm, np.ndarray) or pcm.dtype != np.int16 or pcm.ndim != 1:
        raise ValueError("pcm must be a one-dimensional numpy int16 array")
    count = _frame_count(len(pcm), profile)
    frame, step, nfft = (int(profile[key]) for key in ("frame_length", "frame_step", "nfft"))
    filters, ceps = int(profile["num_filters"]), int(profile["num_ceps"])
    x = pcm.astype(np.float64) / float(profile["pcm_divisor"])
    y = sigproc.preemphasis(x, float(profile["preemphasis"])) if len(x) else x.copy()
    starts = np.arange(count, dtype=np.int64) * step
    if count:
        frames = sigproc.framesig(y, frame, step, winfunc=lambda length: np.ones(length))[:count]
        windowed = sigproc.framesig(y, frame, step, winfunc=np.hamming)[:count]
        spectrum = np.fft.rfft(windowed, nfft)
        power = sigproc.powspec(windowed, nfft)
    else:
        frames = np.empty((0, frame), dtype=np.float64)
        windowed = frames.copy()
        spectrum = np.empty((0, nfft // 2 + 1), dtype=np.complex128)
        power = np.empty((0, nfft // 2 + 1), dtype=np.float64)
    bank = psf_base.get_filterbanks(filters, nfft, int(profile["sample_rate"]),
                                   float(profile["lowfreq"]), float(profile["highfreq"]))
    energies = np.dot(power, bank.T)
    frame_energy = np.sum(power, axis=1)
    log_mel = np.log(_log_input(energies, profile))
    raw_dct = (dct(log_mel, type=2, axis=1, norm="ortho")[:, :ceps]
               if count else np.empty((0, ceps), dtype=np.float64))
    result = psf_base.lifter(raw_dct.copy(), int(profile["lifter"]))
    if bool(profile["append_energy"]):
        result[:, 0] = np.log(_log_input(frame_energy, profile))
    return {
        "input_float": x, "preemphasis": y, "frame_starts": starts,
        "frames": frames, "windowed": windowed, "fft": spectrum, "power": power,
        "mel_energies": energies, "log_mel": log_mel, "dct": raw_dct,
        "frame_energy": frame_energy, "mfcc": result,
    }


def package_native_output(pcm: np.ndarray, profile: Mapping[str, Any]) -> np.ndarray:
    """Unmodified mfcc() with explicit arguments and native pad/zero semantics.

    For the main profile this intentionally retains native tail padding and
    machine-epsilon exact-zero replacement, so policy differences are expected.
    It is not the adapted primary pass/fail comparator.
    """
    if not isinstance(pcm, np.ndarray) or pcm.dtype != np.int16 or pcm.ndim != 1:
        raise ValueError("pcm must be a one-dimensional numpy int16 array")
    if not len(pcm):
        raise ValueError("The unmodified package MFCC does not accept empty PCM")
    sample_rate = int(profile["sample_rate"])
    return psf_base.mfcc(
        pcm.astype(np.float64) / float(profile["pcm_divisor"]),
        samplerate=sample_rate, winlen=int(profile["frame_length"]) / sample_rate,
        winstep=int(profile["frame_step"]) / sample_rate,
        numcep=int(profile["num_ceps"]), nfilt=int(profile["num_filters"]),
        nfft=int(profile["nfft"]), lowfreq=float(profile["lowfreq"]),
        highfreq=float(profile["highfreq"]), preemph=float(profile["preemphasis"]),
        ceplifter=int(profile["lifter"]), appendEnergy=bool(profile["append_energy"]),
        winfunc=np.hamming,
    )


def input_contract_checks(compute: Callable[..., Mapping[str, np.ndarray]],
                          profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ensure inappropriate PCM/configuration cannot silently become results."""
    records = []
    wrong_profile = dict(profile)
    wrong_profile["preemphasis"] = 0.97
    cases = (
        ("reject_float_pcm", np.zeros(512, dtype=np.float64), profile),
        ("reject_stereo_pcm", np.zeros((512, 2), dtype=np.int16), profile),
        ("reject_int32_pcm", np.zeros(512, dtype=np.int32), profile),
        ("reject_python_list_pcm", [0] * 512, profile),
        ("reject_silent_profile_mutation", np.zeros(512, dtype=np.int16), wrong_profile),
    )
    if profile["frame_policy"] == "ceil_zero_pad":
        cases += (("reject_empty_native_profile", np.empty(0, dtype=np.int16), profile),)
    for name, pcm, configuration in cases:
        try:
            compute(pcm, configuration)
        except (TypeError, ValueError) as error:
            records.append({"name": name, "passed": True,
                            "observed_exception": type(error).__name__, "message": str(error)})
        except Exception as error:  # An incidental crash is not a contract check pass.
            records.append({"name": name, "passed": False,
                            "observed_exception": type(error).__name__, "message": str(error)})
        else:
            records.append({"name": name, "passed": False, "error": "invalid input accepted"})
    return records


def analytic_checks(pcm: np.ndarray, profile: Mapping[str, Any],
                    actual: Mapping[str, np.ndarray],
                    coefficients: Mapping[str, np.ndarray] | None = None,
                    atol: float = 1e-10, rtol: float = 1e-10) -> list[dict[str, Any]]:
    """Supplement library agreement with explicit indexing and scalar sums.

    FFT and DCT checks use math.fsum/math.sin/math.cos on selected frames/bins,
    not NumPy FFT or SciPy DCT. These checks isolate transform correctness;
    package stage checks separately cover upstream inputs to those transforms.
    """
    records: list[dict[str, Any]] = []

    def check(name: str, value: Any, expected: Any, exact: bool = False) -> None:
        record = _metric(value, expected, 0.0 if exact else atol, 0.0 if exact else rtol)
        record["name"] = name
        records.append(record)

    count = _frame_count(len(pcm), profile)
    frame, step, nfft = (int(profile[key]) for key in ("frame_length", "frame_step", "nfft"))
    filters, ceps = int(profile["num_filters"]), int(profile["num_ceps"])
    shapes = {
        "input_float": (len(pcm),), "preemphasis": (len(pcm),),
        "frame_starts": (count,), "frames": (count, frame), "windowed": (count, frame),
        "fft": (count, nfft // 2 + 1), "power": (count, nfft // 2 + 1),
        "mel_energies": (count, filters), "log_mel": (count, filters),
        "dct": (count, ceps), "frame_energy": (count,), "mfcc": (count, ceps),
    }
    for stage, expected_shape in shapes.items():
        data = actual.get(stage)
        shape_ok = data is not None and np.shape(data) == expected_shape
        finite = data is not None and bool(np.all(np.isfinite(data)))
        expected_dtype = np.dtype("int64" if stage == "frame_starts" else "complex128" if stage == "fft" else "float64")
        dtype_ok = data is not None and np.asarray(data).dtype == expected_dtype
        records.append({"name": f"shape_finite_{stage}", "expected_shape": list(expected_shape),
                        "actual_shape": list(np.shape(data)) if data is not None else None,
                        "expected_dtype": str(expected_dtype),
                        "actual_dtype": str(np.asarray(data).dtype) if data is not None else None,
                        "shape_ok": shape_ok, "finite": finite, "dtype_ok": dtype_ok,
                        "passed": shape_ok and finite and dtype_ok})
    if not all(item["passed"] for item in records):
        return records
    check("frame_starts_exact", actual["frame_starts"], np.arange(count) * step, exact=True)
    known_boundaries = {0: 0, 511: 0, 512: 1, 671: 1, 672: 2, 832: 3}
    if profile["frame_policy"] == "full_frames_only" and len(pcm) in known_boundaries:
        check("specified_boundary_frame_count", actual["mfcc"].shape[0], known_boundaries[len(pcm)], exact=True)
    for stage in ("power", "mel_energies", "frame_energy"):
        records.append({"name": f"nonnegative_{stage}",
                        "passed": bool(np.all(actual[stage] >= 0.0))})
    divisor, alpha = float(profile["pcm_divisor"]), float(profile["preemphasis"])
    indexes = sorted({index for index in (0, 1, 159, 160, 510, 511, 512, len(pcm) - 1)
                      if 0 <= index < len(pcm)})
    expected_input = [int(pcm[index]) / divisor for index in indexes]
    expected_pre = [int(pcm[index]) / divisor
                    - (alpha * int(pcm[index - 1]) / divisor if index else 0.0)
                    for index in indexes]
    check("pcm_normalization_scalar_samples", actual["input_float"][indexes], expected_input)
    check("continuous_preemphasis_scalar_samples", actual["preemphasis"][indexes], expected_pre)
    peak_bound = (1.0 + alpha) * 32768.0 / divisor
    observed_peak = float(np.max(np.abs(actual["preemphasis"]))) if len(pcm) else 0.0
    records.append({"name": "preemphasis_headroom_bound", "peak_abs": observed_peak,
                    "bound": peak_bound, "passed": observed_peak <= peak_bound + atol})
    if coefficients is not None:
        window = [0.54 - 0.46 * math.cos(2.0 * math.pi * index / (frame - 1))
                  for index in range(frame)]
        check("symmetric_hamming_scalar_formula", coefficients["window"], window)
        check("mel_bin_edges_exact", coefficients["mel_edges"], EXPECTED_MEL_EDGES, exact=True)
    if not count:
        return records

    selected_frames = sorted({0, count // 2, count - 1})
    selected_bins = (0, 1, 7, 31, 32, 33, 128, nfft // 2)
    expected_fft = []
    expected_power = []
    fft_values = []
    power_values = []
    for frame_index in selected_frames:
        row = actual["windowed"][frame_index]
        for frequency_bin in selected_bins:
            # Integer modulo limits trigonometric range reduction error.
            # Exact quarter-turns prevent sin(pi*n) residuals from masquerading
            # as imaginary Nyquist content on large integer-amplitude probes.
            cosine, sine = [], []
            cardinal = {0: (1.0, 0.0), nfft // 4: (0.0, 1.0),
                        nfft // 2: (-1.0, 0.0), 3 * nfft // 4: (0.0, -1.0)}
            for index in range(len(row)):
                phase_index = (frequency_bin * index) % nfft
                if phase_index in cardinal:
                    cos_value, sin_value = cardinal[phase_index]
                else:
                    angle = 2.0 * math.pi * phase_index / nfft
                    cos_value, sin_value = math.cos(angle), math.sin(angle)
                cosine.append(cos_value)
                sine.append(sin_value)
            real = math.fsum(float(value) * cosine[index] for index, value in enumerate(row))
            imag = -math.fsum(float(value) * sine[index] for index, value in enumerate(row))
            expected_fft.append(complex(real, imag))
            expected_power.append((real * real + imag * imag) / nfft)
            fft_values.append(actual["fft"][frame_index, frequency_bin])
            power_values.append(actual["power"][frame_index, frequency_bin])
    check("scalar_dft_selected_bins_sign_scale", fft_values, expected_fft)
    check("scalar_dft_power_divisor_no_double", power_values, expected_power)
    expected_dct = []
    for frame_index in selected_frames:
        row = actual["log_mel"][frame_index]
        expected_dct.append([
            math.sqrt((1.0 if coefficient == 0 else 2.0) / filters)
            * math.fsum(float(value) * math.cos(math.pi * coefficient * (index + 0.5) / filters)
                        for index, value in enumerate(row))
            for coefficient in range(ceps)
        ])
    check("scalar_dct_orthonormal_c0_to_c12", actual["dct"][selected_frames], expected_dct)
    frame_sum = [math.fsum(float(value) for value in actual["power"][index]) for index in selected_frames]
    check("frame_energy_undoubled_power_sum", actual["frame_energy"][selected_frames], frame_sum)
    # With the signed, unnormalized DFT, a doubled interior half-spectrum obeys
    # Parseval; the stored power itself must remain undoubled.
    half_sum = []
    sample_sum = []
    for index in selected_frames:
        row = actual["power"][index]
        half_sum.append(float(row[0]) + float(row[-1]) + 2.0 * math.fsum(float(v) for v in row[1:-1]))
        sample_sum.append(math.fsum(float(v) ** 2 for v in actual["windowed"][index]))
    check("parseval_fft_normalization", half_sum, sample_sum)
    if not np.any(pcm):
        log_floor = math.log(float(profile["log_floor"]))
        expected = np.zeros((count, ceps), dtype=np.float64)
        expected[:, 0] = math.sqrt(filters) * log_floor
        check("silence_analytical_log_floor", actual["log_mel"], np.full((count, filters), log_floor))
        check("silence_analytical_raw_c0_and_zero_higher", actual["dct"], expected)
        if bool(profile["append_energy"]):
            expected[:, 0] = log_floor
        check("silence_analytical_final_output", actual["mfcc"], expected)
    return records
