"""Static Python float64 / C float32 MFCC evidence figures."""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

try:
    from .compare import load_arrays
except ImportError:
    from compare import load_arrays


def plot_case(reference_dir: Path, c_dir: Path, out_dir: Path, case_id: str) -> list[Path]:
    """Render shared-range MFCC heatmaps and per-coefficient error statistics."""
    reference, _ = load_arrays(reference_dir, reference=True)
    actual, _ = load_arrays(c_dir)
    python_mfcc = reference["mfcc"].astype(np.float64)
    c_mfcc = actual["mfcc"].astype(np.float64)
    if python_mfcc.shape != c_mfcc.shape or not len(python_mfcc):
        raise ValueError("MFCC plots require equal, nonempty frame/coefficient arrays")
    if not (np.isfinite(python_mfcc).all() and np.isfinite(c_mfcc).all()):
        raise ValueError("Do not plot nonfinite numerical output as successful evidence")
    difference = c_mfcc - python_mfcc
    maximum = float(np.max(np.abs(difference)))
    rmse = float(np.sqrt(np.mean(difference ** 2)))
    low = float(min(python_mfcc.min(), c_mfcc.min()))
    high = float(max(python_mfcc.max(), c_mfcc.max()))
    if low == high:
        low, high = low - 1.0, high + 1.0
    error_scale = max(maximum, 1e-12)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(case_id))
    paths = []
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10}):
        figure, axes = plt.subplots(3, 1, figsize=(13, 10), constrained_layout=True)
        for axis, values, title in (
            (axes[0], python_mfcc, "Python float64 reference"),
            (axes[1], c_mfcc, "C float32 result"),
        ):
            image = axis.imshow(values.T, origin="lower", aspect="auto", interpolation="nearest",
                                vmin=low, vmax=high, cmap="viridis")
            axis.set_title(title + " — shared color limits", loc="left")
            figure.colorbar(image, ax=axis, label="MFCC value")
        image = axes[2].imshow(difference.T, origin="lower", aspect="auto", interpolation="nearest",
                               vmin=-error_scale, vmax=error_scale, cmap="RdBu_r")
        axes[2].set_title(f"C - Python — separate symmetric error scale; max |error|={maximum:.3e}, RMSE={rmse:.3e}", loc="left")
        figure.colorbar(image, ax=axes[2], label="MFCC difference")
        for axis in axes:
            axis.set_xlabel("Frame index (hop = 160 samples, 10 ms)")
            axis.set_ylabel("Coefficient index")
            axis.set_yticks([0, 3, 6, 9, 12])
        figure.suptitle(f"{case_id}: raw C0…C12, no lifter or energy replacement", fontsize=14)
        for suffix in ("png", "pdf"):
            path = out_dir / f"{safe_id}_python_c_mfcc.{suffix}"
            figure.savefig(path, dpi=180, metadata={"Title": f"Python/C MFCC comparison {case_id}"})
            paths.append(path)
        plt.close(figure)
        per_max = np.max(np.abs(difference), axis=0)
        per_rmse = np.sqrt(np.mean(difference ** 2, axis=0))
        figure, axis = plt.subplots(figsize=(11, 4.8), constrained_layout=True)
        index = np.arange(13)
        axis.bar(index - 0.18, per_max, width=0.36, label="Maximum absolute error")
        axis.bar(index + 0.18, per_rmse, width=0.36, label="RMSE")
        axis.set_xticks(index, [f"C{value}" for value in index])
        axis.set_xlabel("Coefficient (frame axis aggregated)")
        axis.set_ylabel("MFCC error, C float32 minus Python float64")
        axis.set_title(f"{case_id}: per-coefficient numerical difference")
        axis.ticklabel_format(axis="y", style="sci", scilimits=(-3, 3))
        axis.grid(axis="y", alpha=0.25)
        axis.set_axisbelow(True)
        axis.legend()
        for suffix in ("png", "pdf"):
            path = out_dir / f"{safe_id}_coefficient_errors.{suffix}"
            figure.savefig(path, dpi=180, metadata={"Title": f"Per-coefficient Python/C error {case_id}"})
            paths.append(path)
        plt.close(figure)
    return paths
