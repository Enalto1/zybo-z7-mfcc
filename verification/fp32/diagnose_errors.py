"""Attribute recorded vendor-IP errors without changing the DUT or acceptance.

The reconstructed float64 paths are diagnostics of saved operands, not hardware
outputs. Original Python/C inputs, answers, thresholds and run artifacts are read
only. A new output directory is mandatory.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def statistics(delta):
    return {"max_abs_error": float(np.max(np.abs(delta))) if delta.size else 0.,
            "rmse": float(np.sqrt(np.mean(np.abs(delta) ** 2))) if delta.size else 0.}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.run_dir.resolve()
    out = args.output.resolve()
    build = Path(__file__).resolve().parents[3] / "build/fp32_hw"
    if not out.is_relative_to(build) or out == build or out.is_relative_to(root) or root.is_relative_to(out):
        raise ValueError("Use a separate new diagnostic directory under build/fp32_hw")
    out.mkdir(parents=True, exist_ok=False)
    run = json.loads((root / "run_manifest.json").read_text())
    if run["status"] not in ("passed", "completed_with_numerical_failures"):
        raise ValueError("A completed simulation/comparison is required")
    freeze = json.loads((root / "freeze.json").read_text())
    comparison = json.loads((root / "comparison.json").read_text())
    artifacts = json.loads((root / "artifact_manifest.json").read_text())
    verified = {}

    def recorded(relative):
        path = root / relative
        digest = sha(path)
        if artifacts.get(relative) != digest:
            raise ValueError(f"Run artifact changed: {relative}")
        verified[relative] = digest
        return path

    for name in ("run_manifest.json", "freeze.json", "comparison.json", "tolerances.json"):
        recorded(name)
    log_tolerance = json.loads((root / "tolerances.json").read_text())["stages"]["log_mel"]
    run_coefficients = json.loads(recorded("coefficients/coefficient_manifest.json").read_text())
    if run_coefficients["profile_id"] != "comparison_raw13":
        raise ValueError("Unexpected hardware coefficient profile")
    for table in run_coefficients["tables"].values():
        path = recorded("coefficients/" + table["file"])
        if sha(path) != table["sha256"]:
            raise ValueError("Hardware run coefficient ROM hash differs")
    croot = Path(freeze["c_root"])
    cindex = json.loads((croot / "artifact_manifest.json").read_text())
    if sha(croot / "artifact_manifest.json") != "e4abda3effe428e36f055965179d33e75e86c3c751753625952c63f6b3c41d85":
        raise ValueError("Frozen C identity differs")
    coefficient_hashes = {}

    def coefficient(name, shape):
        relative = f"coefficients/{name}.bin"
        path = croot / relative
        digest = sha(path)
        if cindex.get(relative) != digest:
            raise ValueError(f"C coefficient changed: {name}")
        if run_coefficients["verified_source_artifacts"].get(relative) != digest:
            raise ValueError(f"Diagnostic coefficient is not bound to the hardware run: {name}")
        coefficient_hashes[relative] = digest
        if path.stat().st_size != int(np.prod(shape))*4:
            raise ValueError(f"Invalid coefficient byte length: {name}")
        return np.fromfile(path, dtype="<f4").reshape(shape).astype(np.float64)

    mel = coefficient("mel_filters", (26, 257))
    cosine = coefficient("dct_cosine", (13, 26))
    scale = coefficient("dct_scale", (13,))
    floor = float(coefficient("log_floor", (1,))[0])
    records = []
    for case, verdict in zip(freeze["cases"], comparison["cases"], strict=True):
        if case["id"] != verdict["id"]:
            raise ValueError("Case order differs")
        if not case["frames"] or verdict["first_failing_stage"] is None:
            continue
        refdir = Path(case["python_reference"])
        reference_index_path = refdir / "arrays.json"
        reference_index_key = "python/" + reference_index_path.relative_to(Path(freeze["python_root"])).as_posix()
        if freeze["reference_hashes"].get(reference_index_key) != sha(reference_index_path):
            raise ValueError("Python array schema changed")
        refindex = json.loads(reference_index_path.read_text())["arrays"]
        hw, py = {}, {}
        widths = {"windowed":512, "fft":257, "power":257, "mel_energies":26, "log_mel":26, "mfcc":13}
        for name in ("windowed", "fft", "power", "mel_energies", "log_mel", "mfcc"):
            entry = refindex[name]
            expected_shape = [case["frames"], widths[name]]
            expected_dtype = "<c16" if name == "fft" else "<f8"
            if entry["shape"] != expected_shape or np.dtype(entry["dtype"]) != np.dtype(expected_dtype):
                raise ValueError(f"Unexpected frozen reference shape/dtype: {name}")
            path = refdir / entry["file"]
            relative = "python/" + path.relative_to(Path(freeze["python_root"])).as_posix()
            if freeze["reference_hashes"].get(relative) != sha(path):
                raise ValueError(f"Python answer changed: {relative}")
            if path.stat().st_size != int(np.prod(expected_shape))*np.dtype(expected_dtype).itemsize:
                raise ValueError(f"Invalid Python stage byte length: {name}")
            py[name] = np.fromfile(path, dtype=entry["dtype"]).reshape(entry["shape"])
            dtype = "<c8" if name == "fft" else "<f4"
            path = recorded(f"arrays/{case['id']}/{name}.bin")
            if path.stat().st_size != int(np.prod(expected_shape))*np.dtype(dtype).itemsize:
                raise ValueError(f"Invalid hardware stage byte length: {name}")
            hw[name] = np.fromfile(path, dtype=dtype).reshape(entry["shape"]).astype(py[name].dtype)
        precise_fft_of_hw_window = np.fft.rfft(hw["windowed"], n=512, axis=1)
        precise_power_of_hw_fft = (hw["fft"].real**2 + hw["fft"].imag**2) / 512.
        precise_mel_of_hw_power = hw["power"] @ mel.T
        precise_log_of_hw_mel = np.log(np.maximum(hw["mel_energies"], floor))
        precise_dct_of_hw_log = (hw["log_mel"] @ cosine.T) * scale
        # This is an explicitly diagnostic counterfactual using the saved window.
        precise_pipeline_power = (precise_fft_of_hw_window.real**2 + precise_fft_of_hw_window.imag**2) / 512.
        precise_pipeline_mel = precise_pipeline_power @ mel.T
        precise_pipeline_log = np.log(np.maximum(precise_pipeline_mel, floor))
        precise_pipeline_mfcc = (precise_pipeline_log @ cosine.T) * scale
        log_error = np.abs(hw["log_mel"] - py["log_mel"])
        index = np.unravel_index(int(log_error.argmax()), log_error.shape)
        worst = {"frame": int(index[0]), "filter": int(index[1]),
                 "python_mel_energy": float(py["mel_energies"][index]),
                 "hardware_mel_energy": float(hw["mel_energies"][index]),
                 "python_log": float(py["log_mel"][index]),
                 "hardware_log": float(hw["log_mel"][index]),
                 "float64_log_of_hardware_mel": float(precise_log_of_hw_mel[index]),
                 "vendor_log_residual": float(hw["log_mel"][index] - precise_log_of_hw_mel[index]),
                 "upstream_energy_log_difference": float(precise_log_of_hw_mel[index] - py["log_mel"][index])}
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        frame = int(index[0])
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), constrained_layout=True)
        filters = np.arange(26)
        axes[0].semilogy(filters, np.maximum(py["mel_energies"][frame], np.finfo(float).tiny),
                         "o-", label="Python float64 Mel energy")
        axes[0].semilogy(filters, np.maximum(hw["mel_energies"][frame], np.finfo(float).tiny),
                         "x-", label="Recorded IP Mel energy")
        axes[0].axhline(floor, color="black", linestyle="--", label="Unchanged log floor")
        axes[0].set_xlabel("Mel filter index"); axes[0].set_ylabel("Energy before floor")
        axes[0].legend(fontsize=8)
        axes[1].bar(filters-.18, precise_log_of_hw_mel[frame]-py["log_mel"][frame], .36,
                    label="Energy difference propagated through ln (diagnostic)")
        axes[1].bar(filters+.18, hw["log_mel"][frame]-precise_log_of_hw_mel[frame], .36,
                    label="Vendor ln residual on its recorded operand")
        axes[1].plot(filters, hw["log_mel"][frame]-py["log_mel"][frame], "k.-", linewidth=1,
                     label="Recorded HW - Python total (acceptance applies here)")
        bound = log_tolerance["atol"] + log_tolerance["rtol"]*np.abs(py["log_mel"][frame])
        axes[1].plot(filters, bound, "r--", linewidth=1, label="Frozen tolerance for recorded total")
        axes[1].plot(filters, -bound, "r--", linewidth=1)
        axes[1].set_xlabel("Mel filter index"); axes[1].set_ylabel("Signed log difference")
        axes[1].legend(fontsize=7)
        fig.suptitle(f"{case['id']} | frame {frame} | recorded results and diagnostic decomposition")
        fig.savefig(out / f"{case['id']}_energy_diagnosis.png", dpi=160)
        plt.close(fig)
        records.append({"id": case["id"], "frames": case["frames"],
            "first_failed_acceptance_stage": verdict["first_failing_stage"],
            "known_pc_c_failure": verdict["known_pc_c_failure"],
            "actual_mfcc_vs_python": statistics(hw["mfcc"] - py["mfcc"]),
            "fft_vs_float64_fft_of_actual_window": statistics(hw["fft"] - precise_fft_of_hw_window),
            "power_rounding_residual": statistics(hw["power"] - precise_power_of_hw_fft),
            "mel_accumulation_residual": statistics(hw["mel_energies"] - precise_mel_of_hw_power),
            "vendor_log_residual": statistics(hw["log_mel"] - precise_log_of_hw_mel),
            "upstream_energy_log_difference": statistics(precise_log_of_hw_mel - py["log_mel"]),
            "dct_rounding_residual": statistics(hw["mfcc"] - precise_dct_of_hw_log),
            "diagnostic_float64_downstream_of_actual_window_vs_python": statistics(precise_pipeline_mfcc - py["mfcc"]),
            "worst_log": worst})
    result = {"created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run": str(root), "hardware_results_modified": False, "acceptance_changed": False,
        "diagnostic_only": True, "notice": "Float64 reconstructions are not hardware results and do not change any verdict.",
        "diagnostic_sum": "NumPy float64 matrix products; not the sequential hardware sum order",
        "binary32_log_floor_promoted_to_float64": floor,
        "verified_run_artifacts": verified, "verified_coefficient_hashes": coefficient_hashes,
        "cases": records}
    shutil.copyfile(__file__, out / "diagnose_errors_used.py")
    (out / "diagnosis.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    (out / "artifact_manifest.json").write_text(json.dumps({p.name: sha(p) for p in out.iterdir() if p.is_file()}, indent=2) + "\n")
    print(json.dumps({"output": str(out), "diagnosed_failed_cases": len(records)}))


if __name__ == "__main__":
    main()
