"""Export measured MFCC comparisons as static, headless figures.

Inputs are frame-major raw C0..C12 arrays from the comparison profile.
The comparison must already have the documented package-policy adapters applied;
this module does not alter, align, normalize, or truncate either input.
"""

from __future__ import annotations

from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, ScalarFormatter
import numpy as np


def _comparison_arrays(reference: np.ndarray, comparison: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Reject ambiguous axes, missing frames, complex data and nonfinite values."""
    if np.iscomplexobj(reference) or np.iscomplexobj(comparison):
        raise ValueError("MFCC plotting inputs must be real arrays.")
    reference = np.asarray(reference, dtype=np.float64)
    comparison = np.asarray(comparison, dtype=np.float64)
    if reference.ndim != 2 or reference.shape[1] != 13 or reference.shape[0] == 0:
        raise ValueError("reference must have shape (nonzero frame count, 13).")
    if comparison.shape != reference.shape:
        raise ValueError("comparison must have exactly the reference (frames, 13) shape.")
    if not np.isfinite(reference).all() or not np.isfinite(comparison).all():
        raise ValueError("Nonfinite MFCC values must be reported as failures before plotting.")
    return reference, comparison


def _export(fig: plt.Figure, destination: Path, stem: str) -> list[Path]:
    outputs = [destination / f"{stem}.png", destination / f"{stem}.pdf"]
    try:
        fig.savefig(outputs[0], dpi=300, facecolor="white")
        fig.savefig(outputs[1], facecolor="white", metadata={"Creator": "MFCC Python reference"})
    finally:
        plt.close(fig)
    return outputs


def plot_development(
    reference: np.ndarray,
    comparison: np.ndarray,
    output_dir: str | Path,
    clip_id: str,
    hop: int = 160,
    sr: int = 16000,
) -> list[Path]:
    """Save heatmaps and measured coefficient errors as PNG (300 dpi) and PDF.

    ``reference`` and ``comparison`` are matching, finite, nonempty F x 13
    arrays, ordered C0..C12. ``comparison`` is the explicitly adapted package
    result, not its unmodified default MFCC output. Errors are comparison minus
    reference. The x-axis identifies frame start times (index * hop / sr), not
    frame centers or the complete audio duration. Returns the four output paths.
    """
    reference, comparison = _comparison_arrays(reference, comparison)
    if isinstance(hop, bool) or isinstance(sr, bool) or not isinstance(hop, (int, np.integer)) or not isinstance(sr, (int, np.integer)) or hop <= 0 or sr <= 0:
        raise ValueError("hop and sr must be positive integers.")
    if not isinstance(clip_id, str) or not clip_id.strip():
        raise ValueError("clip_id must be a nonempty string.")

    difference = comparison - reference
    if not np.isfinite(difference).all():
        raise ValueError("Difference overflowed; no numerical-error figure was generated.")
    max_abs = float(np.max(np.abs(difference)))
    # Scaling first prevents overflow when squaring large finite differences.
    rmse = max_abs * float(np.sqrt(np.mean((difference / max_abs) ** 2))) if max_abs else 0.0
    per_coefficient_max = np.max(np.abs(difference), axis=0)
    per_coefficient_rmse = np.zeros(13, dtype=np.float64)
    nonzero = per_coefficient_max != 0.0
    per_coefficient_rmse[nonzero] = per_coefficient_max[nonzero] * np.sqrt(
        np.mean((difference[:, nonzero] / per_coefficient_max[nonzero]) ** 2, axis=0)
    )

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    safe_clip_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", clip_id).strip("._") or "clip"
    frame_count = reference.shape[0]
    seconds_per_frame = hop / sr
    extent = (-0.5 * seconds_per_frame, (frame_count - 0.5) * seconds_per_frame, -0.5, 12.5)
    value_min = min(float(reference.min()), float(comparison.min()))
    value_max = max(float(reference.max()), float(comparison.max()))
    if value_min == value_max:
        padding = max(abs(value_min), 1.0) * 1e-6
        value_min, value_max = value_min - padding, value_max + padding
    error_limit = max_abs if max_abs else np.finfo(np.float64).eps
    figure_style = {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "pdf.fonttype": 42,
        "savefig.bbox": None,
    }
    outputs: list[Path] = []
    with plt.rc_context(figure_style):
        fig, axes = plt.subplots(3, 1, figsize=(10.5, 8.6), sharex=True, layout="constrained")
        fig.suptitle(f"Static MFCC comparison | {clip_id}\n{frame_count} frames, C0–C12, {sr:,} Hz, hop {hop} samples", fontsize=13)
        panels = (
            (reference, "Reference: explicit float64 pipeline", "viridis", value_min, value_max),
            (comparison, "Comparison: package with explicit policy adapters", "viridis", value_min, value_max),
            (difference, f"Difference: adapted package − reference | max |Δ| = {max_abs:.3e}, RMSE = {rmse:.3e}", "RdBu_r", -error_limit, error_limit),
        )
        for ax, (values, title, color_map, low, high) in zip(axes, panels):
            artist = ax.imshow(values.T, origin="lower", aspect="auto", interpolation="nearest", extent=extent, cmap=color_map, vmin=low, vmax=high, rasterized=True)
            ax.set_title(title, loc="left")
            ax.set_ylabel("Coefficient index")
            ax.set_yticks(np.arange(13))
            colorbar = fig.colorbar(artist, ax=ax, pad=0.015, fraction=0.035)
            colorbar.set_label("MFCC value" if ax is not axes[2] else "Signed difference")
            if ax is axes[2]:
                colorbar.formatter = ScalarFormatter(useMathText=True)
                colorbar.formatter.set_powerlimits((0, 0))
                colorbar.update_ticks()
        axes[-1].set_xlabel("Frame start time (s)")
        axes[-1].xaxis.set_major_locator(MaxNLocator(nbins=8, min_n_ticks=2))
        frame_axis = axes[0].secondary_xaxis(
            "top", functions=(lambda seconds: seconds / seconds_per_frame, lambda frames: frames * seconds_per_frame)
        )
        frame_axis.set_xlabel("Frame index")
        frame_axis.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True, min_n_ticks=2))
        if max_abs == 0.0:
            axes[2].text(0.99, 0.05, "All differences exactly zero; display limits ±machine epsilon", transform=axes[2].transAxes, ha="right", va="bottom", fontsize=8, bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "none"})
        outputs.extend(_export(fig, destination, f"{safe_clip_id}_mfcc_comparison"))

        fig, ax = plt.subplots(figsize=(10.5, 4.6), layout="constrained")
        indices = np.arange(13)
        ax.bar(indices - 0.19, per_coefficient_max, width=0.38, color="#2563a6", label="Maximum absolute error")
        ax.bar(indices + 0.19, per_coefficient_rmse, width=0.38, color="#d88424", label="RMSE")
        ax.set_title(f"Measured MFCC differences by coefficient | {clip_id}\nAdapted package − reference; all {frame_count} frames", loc="left")
        ax.set_xlabel("Coefficient index")
        ax.set_ylabel("Absolute MFCC difference")
        ax.set_xticks(indices, [f"C{i}" for i in indices])
        ax.ticklabel_format(axis="y", style="sci", scilimits=(0, 0), useMathText=True)
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#d7dde5", linewidth=0.6)
        ax.legend(loc="upper right", frameon=False)
        ax.set_ylim(0.0, max_abs * 1.28 if max_abs else np.finfo(np.float64).eps)
        if max_abs == 0.0:
            ax.text(0.5, 0.5, "All measured coefficient differences are exactly zero", transform=ax.transAxes, ha="center", va="center")
        outputs.extend(_export(fig, destination, f"{safe_clip_id}_mfcc_errors"))
    return outputs
