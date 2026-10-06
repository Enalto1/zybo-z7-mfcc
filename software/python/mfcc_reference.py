"""Stage-visible float64 MFCC reference for the two recorded project profiles.

The equations are implemented from docs/MFCC_SPEC.md. No MFCC implementation
is imported or copied from python_speech_features; that package is an external
comparison in the verification harness. NumPy and SciPy are shared numerical
dependencies, so agreement with it is not independent algorithm certification.

Input is an already decoded mono PCM16 array at the profile's sample rate.
This module does not resample, crop, normalize peaks, read files, or write files.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.fft import rfft


# This is deliberately a pair of versioned experiment contracts, not a general
# configurable feature library. Every algorithm setting must be present in JSON.
_COMMON = {
    "schema_version": 1,
    "sample_rate": 16000,
    "frame_length": 512,
    "frame_step": 160,
    "nfft": 512,
    "num_filters": 26,
    "num_ceps": 13,
    "preemphasis": 0.95,
    "initial_previous_sample": 0.0,
    "window": "symmetric_hamming",
    "fft_norm": "backward",
    "fft_sign": "negative",
    "power_divisor": 512,
    "one_sided_double": False,
    "mel_scale": "htk",
    "lowfreq": 0.0,
    "highfreq": 8000.0,
    "mel_bin_rule": "floor((nfft+1)*hz/sample_rate)",
    "mel_normalization": "peak_one_no_area_normalization",
    "log_base": "natural",
    "dct_type": 2,
    "dct_norm": "ortho",
    "delta": False,
    "delta_delta": False,
    "cmvn": False,
    "arithmetic_dtype": "float64",
}
_VARIANTS = {
    "comparison_raw13": {
        "spec_id": "mfcc-raw13-v0.1-draft",
        "pcm_divisor": 32768.0,
        "frame_policy": "full_frames_only",
        "empty_input_policy": "zero_frames",
        "log_policy": "floor",
        "log_floor": 1e-12,
        "lifter": 0,
        "append_energy": False,
        "output_order": [f"C{i}" for i in range(13)],
    },
    "github_static13": {
        "spec_id": "github-notebook-static13-psf0.6-v1",
        "pcm_divisor": 1.0,
        "frame_policy": "ceil_zero_pad",
        "empty_input_policy": "reject",
        "log_policy": "replace_zero_only",
        "log_floor": 2.0**-52,
        "lifter": 22,
        "append_energy": True,
        "output_order": ["log_frame_energy"]
        + [f"lifter_C{i}" for i in range(1, 13)],
    },
}


def validate_profile(profile: dict[str, Any]) -> None:
    """Reject incomplete, misspelled, nonfinite, or unsupported settings.

    Numeric integer/float JSON spellings are equivalent where the setting is
    numeric; booleans are never accepted as numeric values. Profile extensions
    require an explicit implementation/schema change rather than silent ignore.
    """
    if not isinstance(profile, dict):
        raise TypeError("profile must be a JSON object/dict")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or profile_id not in _VARIANTS:
        raise ValueError(f"unsupported profile_id: {profile_id!r}")
    expected = dict(_COMMON, **_VARIANTS[profile_id])
    required = set(expected) | {"profile_id", "provenance"}
    if set(profile) != required:
        raise ValueError(
            f"profile keys differ: missing={sorted(required - set(profile))}, "
            f"unknown={sorted(set(profile) - required)}"
        )
    for key, value in expected.items():
        actual = profile[key]
        if isinstance(value, bool):
            valid = type(actual) is bool and actual == value
        elif isinstance(value, (int, float)):
            valid = (
                type(actual) in (int, float)
                and math.isfinite(actual)
                and actual == value
            )
        else:
            valid = type(actual) is type(value) and actual == value
        if not valid:
            raise ValueError(
                f"unsupported {key}={actual!r} for {profile_id}; expected {value!r}"
            )
    provenance = profile["provenance"]
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("provenance must be a nonempty JSON object")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate profile JSON key: {key}")
        result[key] = value
    return result


def load_profile(path: str | Path) -> dict[str, Any]:
    """Read a complete JSON profile and validate it, without applying defaults."""
    def invalid_constant(value: str) -> None:
        raise ValueError(f"nonfinite JSON constant is forbidden: {value}")

    with Path(path).open("r", encoding="utf-8") as handle:
        profile = json.load(
            handle, object_pairs_hook=_json_object, parse_constant=invalid_constant
        )
    validate_profile(profile)
    return profile


def coefficient_tables(profile: dict[str, Any]) -> dict[str, np.ndarray]:
    """Generate float64 tables; edges are int64, all other tables are float64.

    Shapes: window (512,), mel_edges (28,), mel_filters (26,257),
    dct_cosine/dct_matrix (13,26), dct_scale/lifter (13,).
    dct_matrix[c,m] includes the orthonormal scale; dct_cosine does not.
    """
    validate_profile(profile)
    length = int(profile["frame_length"])
    nfft = int(profile["nfft"])
    filters = int(profile["num_filters"])
    ceps = int(profile["num_ceps"])

    sample_index = np.arange(length, dtype=np.float64)
    window = 0.54 - 0.46 * np.cos(2.0 * np.pi * sample_index / (length - 1))
    mel_low = 2595.0 * np.log10(1.0 + profile["lowfreq"] / 700.0)
    mel_high = 2595.0 * np.log10(1.0 + profile["highfreq"] / 700.0)
    mel_points = np.linspace(mel_low, mel_high, filters + 2, dtype=np.float64)
    hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
    edges = np.floor((nfft + 1) * hz_points / profile["sample_rate"]).astype(
        np.int64
    )
    if np.any(np.diff(edges) <= 0):
        raise ValueError("Mel filter edges must have strictly increasing FFT bins")
    if edges[0] < 0 or edges[-1] > nfft // 2:
        raise ValueError("Mel edges are outside the one-sided FFT")

    bins = np.arange(nfft // 2 + 1, dtype=np.float64)[None, :]
    left = edges[:-2, None]
    center = edges[1:-1, None]
    right = edges[2:, None]
    rising = (bins - left) / (center - left)
    falling = (right - bins) / (right - center)
    mel_filters = np.maximum(0.0, np.minimum(rising, falling))

    order = np.arange(ceps, dtype=np.float64)
    mel_index = np.arange(filters, dtype=np.float64)
    cosine = np.cos(np.pi * order[:, None] * (mel_index + 0.5) / filters)
    scale = np.full(ceps, np.sqrt(2.0 / filters), dtype=np.float64)
    scale[0] = np.sqrt(1.0 / filters)
    lifter = np.ones(ceps, dtype=np.float64)
    if profile["lifter"]:
        lifter += (profile["lifter"] / 2.0) * np.sin(
            np.pi * order / profile["lifter"]
        )
    return {
        "window": window,
        "mel_edges": edges,
        "mel_filters": mel_filters,
        "dct_cosine": cosine,
        "dct_scale": scale,
        "dct_matrix": cosine * scale[:, None],
        "lifter": lifter,
    }


def _log_values(energy: np.ndarray, profile: dict[str, Any]) -> np.ndarray:
    if not np.all(np.isfinite(energy)) or np.any(energy < 0):
        raise FloatingPointError("MFCC energy must be finite and nonnegative")
    if profile["log_policy"] == "floor":
        positive = np.maximum(energy, profile["log_floor"])
    else:
        positive = np.where(energy == 0, profile["log_floor"], energy)
    return np.log(positive)


def compute_mfcc(
    pcm: np.ndarray, profile: dict[str, Any]
) -> dict[str, np.ndarray]:
    """Compute every stage for one PCM clip with reset pre-emphasis state.

    Rows of 2-D stages are chronological frames. ``frames`` are pre-emphasized
    samples before the window; any github tail padding occurs AFTER filtering.
    ``fft`` has bins 0..256 and dtype complex128. ``frame_energy`` and
    ``mel_energies`` are raw power sums before any logarithm guard. ``dct`` is
    always raw C0..C12; ``mfcc`` additionally applies the profile's postprocessing.
    Main empty/short clips return zero frames. Github empty input is rejected
    explicitly because the external package's pre-emphasis needs signal[0].
    The caller's input array and profile are not mutated.
    """
    validate_profile(profile)
    if not isinstance(pcm, np.ndarray) or pcm.ndim != 1:
        raise TypeError("pcm must be a one-dimensional NumPy PCM16 array")
    if pcm.dtype.kind != "i" or pcm.dtype.itemsize != 2:
        raise TypeError("pcm must contain signed 16-bit integers; no implicit casting")
    if pcm.size == 0 and profile["empty_input_policy"] == "reject":
        raise ValueError(
            "github_static13 rejects empty input: the comparison package's "
            "pre-emphasis requires signal[0]"
        )

    tables = coefficient_tables(profile)
    x = pcm.astype(np.float64) / profile["pcm_divisor"]
    emphasized = x.copy()
    if x.size:
        emphasized[0] -= profile["preemphasis"] * profile["initial_previous_sample"]
        emphasized[1:] -= profile["preemphasis"] * x[:-1]

    length = int(profile["frame_length"])
    hop = int(profile["frame_step"])
    nfft = int(profile["nfft"])
    if profile["frame_policy"] == "full_frames_only":
        count = 0 if x.size < length else 1 + (x.size - length) // hop
    else:
        count = 1 if x.size <= length else 1 + (x.size - length + hop - 1) // hop
    starts = np.arange(count, dtype=np.int64) * hop
    if count:
        required_length = int(starts[-1]) + length
        framed_signal = np.pad(emphasized, (0, max(0, required_length - x.size)))
        indices = starts[:, None] + np.arange(length, dtype=np.int64)[None, :]
        frames = framed_signal[indices]
    else:
        frames = np.empty((0, length), dtype=np.float64)

    windowed = frames * tables["window"]
    spectrum = (
        rfft(windowed, n=nfft, axis=1, norm=profile["fft_norm"], workers=1)
        if count
        else np.empty((0, nfft // 2 + 1), dtype=np.complex128)
    )
    power = (spectrum.real**2 + spectrum.imag**2) / profile["power_divisor"]
    energy = np.sum(power, axis=1)
    mel_energy = power @ tables["mel_filters"].T
    log_mel = _log_values(mel_energy, profile)
    raw_dct = (log_mel @ tables["dct_cosine"].T) * tables["dct_scale"]
    mfcc = raw_dct * tables["lifter"]
    if profile["append_energy"]:
        mfcc[:, 0] = _log_values(energy, profile)

    stages = {
        "input_float": x,
        "preemphasis": emphasized,
        "frame_starts": starts,
        "frames": frames,
        "windowed": windowed,
        "fft": spectrum,
        "power": power,
        "mel_energies": mel_energy,
        "log_mel": log_mel,
        "dct": raw_dct,
        "frame_energy": energy,
        "mfcc": mfcc,
    }
    for name, values in stages.items():
        if not np.all(np.isfinite(values)):
            raise FloatingPointError(f"nonfinite value in MFCC stage {name}")
    return stages
