"""Compare transaction dumps from actual FP IPs with immutable Python/C stages."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import numpy as np

STAGES = {0: "input_float", 10: "preemphasis", 11: "frames", 12: "windowed",
          1: "fft", 2: "power", 3: "mel_energies", 4: "log_mel", 5: "dct", 6: "mfcc"}
ORDER = [0, 10, 11, 12, 1, 2, 3, 4, 5, 6]

def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

def metric(actual, reference, tolerance):
    error = np.abs(actual-reference)
    bound = tolerance["atol"] + tolerance["rtol"]*np.abs(reference)
    bad = (error > bound) | ~np.isfinite(actual) | ~np.isfinite(reference)
    worst = np.unravel_index(int(error.argmax()), error.shape) if error.size else None
    first = np.argwhere(bad)[0].tolist() if np.any(bad) else None
    def value(arr, idx):
        if idx is None:
            return None
        x = arr[tuple(idx)]
        return [float(x.real), float(x.imag)] if np.iscomplexobj(arr) else float(x)
    return {"passed": not bool(np.any(bad)), "elements": int(actual.size),
            "violations": int(np.count_nonzero(bad)), "nonfinite": int(np.count_nonzero(~np.isfinite(actual))),
            "max_abs_error": float(error.max()) if error.size else 0.0,
            "rmse": float(np.sqrt(np.mean(error**2))) if error.size else 0.0,
            "first_violation_index": first, "worst_index": [int(x) for x in worst] if worst is not None else None,
            "worst_actual": value(actual, worst), "worst_reference": value(reference, worst),
            "worst_bound": float(bound[worst]) if worst is not None else None,
            "tolerance": tolerance}

def figures(out, name, hw, py, pc):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figdir = out / "figures"
    figdir.mkdir(exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(16, 8), constrained_layout=True)
    low, high = float(min(hw.min(), py.min(), pc.min())), float(max(hw.max(), py.max(), pc.max()))
    for ax, values, title in zip(axes[0], [py, pc, hw], ["Python float64", "PC C float32", "Xilinx IP hardware simulation"]):
        im=ax.imshow(values.T, aspect="auto", origin="lower", vmin=low, vmax=high)
        ax.set_title(title); fig.colorbar(im, ax=ax)
    for ax, values, title in zip(axes[1], [hw-py, hw-pc, pc-py], ["Hardware - Python", "Hardware - PC C", "PC C - Python"]):
        limit=float(np.max(np.abs(values))) or 1e-20
        im=ax.imshow(values.T, aspect="auto", origin="lower", cmap="RdBu_r", vmin=-limit, vmax=limit)
        ax.set_title(title); fig.colorbar(im, ax=ax)
    for ax in axes.flat:
        ax.set_xlabel("Frame index"); ax.set_ylabel("Coefficient C0..C12")
    fig.suptitle(f"comparison_raw13 | {name} | actual vendor behavioral simulation")
    fig.savefig(figdir/f"{name}_heatmaps.png", dpi=160); plt.close(fig)
    delta=hw-py
    maxerr=np.max(np.abs(delta), axis=0); rmse=np.sqrt(np.mean(delta**2, axis=0))
    fig, ax=plt.subplots(figsize=(12, 4), constrained_layout=True)
    x=np.arange(13)
    ax.bar(x-.2,maxerr,.4,label="Maximum absolute error")
    ax.bar(x+.2,rmse,.4,label="RMSE")
    ax.set_xticks(x,[f"C{i}" for i in x]); ax.legend()
    ax.set_ylabel("Hardware - Python MFCC absolute error")
    ax.set_title(f"{name} | {len(hw)} frames")
    fig.savefig(figdir/f"{name}_coefficient_errors.png", dpi=160); plt.close(fig)
    return {"max_abs_error": maxerr.tolist(), "rmse": rmse.tolist()}

def compare_run(out):
    out=Path(out)
    manifest=json.loads((out/"freeze.json").read_text())
    tolerances=json.loads((out/"tolerances.json").read_text())["stages"]
    cases={c["number"]:c for c in manifest["cases"]}
    refs={}
    buffers={}
    counts={}
    cycles={}
    for number, case in cases.items():
        directory=Path(case["python_reference"])
        index=json.loads((directory/"arrays.json").read_text())["arrays"]
        for stage, name in STAGES.items():
            entry=index[name]
            reference=np.fromfile(directory/entry["file"],dtype=entry["dtype"]).reshape(entry["shape"])
            refs[number,stage]=reference
            buffers[number,stage]=np.full(reference.shape, complex(np.nan,np.nan) if stage==1 else np.nan,
                                          dtype=np.complex128 if stage==1 else np.float64)
            counts[number,stage]=0
        cycles[number]=[]
    violations=[]
    with (out/"traces.txt").open() as file:
        for line_number,line in enumerate(file,1):
            words=line.split()
            if len(words)!=7:
                raise ValueError(f"Malformed trace line {line_number}")
            number,stage,frame,index=map(int,words[:4])
            key=(number,stage)
            if key not in buffers:
                raise ValueError(f"Unexpected case/stage {key}")
            dest=buffers[key]
            size=dest.shape[1] if dest.ndim==2 else dest.size
            count=counts[key]
            expected=(count//size,count%size) if dest.ndim==2 else (0,count)
            if (frame,index)!=expected or count>=dest.size:
                raise ValueError(f"Trace order/missing/duplicate/out-of-range at line {line_number}: {key} {(frame,index)} != {expected}")
            values=np.array([int(words[4],16),int(words[5],16)],dtype=np.uint32).view(np.float32).astype(np.float64)
            value=complex(*values) if stage==1 else values[0]
            dest.flat[count]=value
            counts[key]=count+1
            if stage==6 and index==12:
                cycles[number].append(int(words[6]))
    result={"protocol_passed": True, "numeric_acceptance_passed": True,
            "evaluation_audio_read": False, "physical_board_accessed": False, "cases": []}
    for number,case in cases.items():
        record={"id":case["id"],"group":case["group"],"samples":case["samples"],"frames":case["frames"],
                "protocol_passed":True,"stages":{},"first_failing_stage":None,
                "known_pc_c_failure":case["id"] in ("fullscale_alternating","tone_bin32_1000hz")}
        arraydir=out/"arrays"/case["id"]
        arraydir.mkdir(parents=True,exist_ok=False)
        array_index={"storage":"headerless little-endian C row-major", "arrays":{}}
        for stage in ORDER:
            name=STAGES[stage]
            hw=buffers[number,stage]
            if counts[number,stage]!=hw.size:
                raise ValueError(f"Missing stage values {case['id']} {name}: {counts[number,stage]}/{hw.size}")
            if not np.isfinite(hw).all():
                raise ValueError(f"Nonfinite hardware stage: {case['id']} {name}")
            py=refs[number,stage]
            dtype="<c8" if stage==1 else "<f4"
            pc=np.fromfile(Path(case["c_reference"])/f"{name}.bin",dtype=dtype).reshape(hw.shape).astype(hw.dtype)
            hw.astype(dtype).tofile(arraydir/f"{name}.bin")
            binary_path=arraydir/f"{name}.bin"
            array_index["arrays"][name]={"file":binary_path.name,"dtype":dtype,"shape":list(hw.shape),
                "sha256":hashlib.sha256(binary_path.read_bytes()).hexdigest(),"finite":True}
            measurement=metric(hw,py,tolerances[name])
            measurement["versus_pc_c"]=metric(hw,pc,tolerances[name])
            bit_dtype = "<u8" if stage == 1 else "<u4"
            measurement["hw_vs_pc_bit_equal_elements"]=int(np.count_nonzero(
                hw.astype(dtype).view(bit_dtype)==pc.astype(dtype).view(bit_dtype)))
            record["stages"][name]=measurement
            if not measurement["passed"] and record["first_failing_stage"] is None:
                record["first_failing_stage"]=name
            if not measurement["passed"]:
                result["numeric_acceptance_passed"]=False
        if not np.array_equal(buffers[number,5], buffers[number,6]):
            raise ValueError("DCT debug values differ from accepted MFCC output")
        intervals=np.diff(cycles[number])
        record["simulated_completion_interval_cycles"]={"count":len(intervals),
                "min":int(intervals.min()) if len(intervals) else None,
                "median":float(np.median(intervals)) if len(intervals) else None,
                "max":int(intervals.max()) if len(intervals) else None,
                "note":"Simulation includes input/output stalls; not measured board time or Fmax."}
        record["passed"]=record["first_failing_stage"] is None
        if case["frames"] and (case["group"]=="development" or not record["passed"]):
            pc=np.fromfile(Path(case["c_reference"])/"mfcc.bin",dtype="<f4").reshape(-1,13).astype(float)
            record["coefficient_errors"]=figures(out,case["id"],buffers[number,6],refs[number,6],pc)
        write_json(arraydir/"validation.json",record)
        write_json(arraydir/"arrays.json",array_index)
        result["cases"].append(record)
    result["total_frames"]=sum(case["frames"] for case in cases.values())
    result["passed_cases"]=sum(case["passed"] for case in result["cases"])
    result["failed_cases"]=[case["id"] for case in result["cases"] if not case["passed"]]
    write_json(out/"comparison.json",result)
    return result
