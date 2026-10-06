"""Audit an immutable precision-study snapshot and export a partial C contract.

This is HOST tooling. Float64 front/back-end calculations below are diagnostics,
not part of the integer C model. No live software/fixed_model import is allowed.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import struct
import sys

import numpy as np

VERSION = "provisional-prec04-t0p975-d20_out20_in16-v1"
CANDIDATE = "d20_out20_in16"
TARGET = "t0p975"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False,
                                   allow_nan=False) + "\n", encoding="utf-8")


def audit(frozen):
    manifest = read(frozen / "run_manifest.json")
    if manifest["status"] != "completed":
        raise ValueError("Frozen precision run is not completed")
    failures = []
    hashes = read(frozen / "artifact_hashes.json")
    checked = {}
    for relative, expected in hashes.items():
        path = (frozen / relative).resolve()
        if not path.is_relative_to(frozen.resolve()):
            raise ValueError("Artifact escapes frozen root")
        actual = sha(path)
        checked[relative] = actual
        if actual != expected:
            failures.append(relative)
    for relative, expected in manifest["source_snapshot_sha256"].items():
        path = frozen / "source_snapshot" / relative
        if sha(path) != expected:
            failures.append("source_snapshot/" + relative)
    if failures:
        raise ValueError("Frozen hash mismatches: " + repr(failures))
    return manifest, {"passed": True, "artifacts_checked": len(checked),
                      "source_files_checked": len(manifest["source_snapshot_sha256"]),
                      "manifest_sha256": sha(frozen / "run_manifest.json"),
                      "artifact_index_sha256": sha(frozen / "artifact_hashes.json"),
                      "artifact_sha256": checked}


def frozen_modules(frozen):
    snapshot = (frozen / "source_snapshot").resolve()
    if any(k.startswith("software.fixed_model") for k in sys.modules):
        raise RuntimeError("Refuse cached fixed model; use a fresh process")
    sys.path.insert(0, str(snapshot))
    modules = {name: importlib.import_module("software.fixed_model." + name)
               for name in ("fft_bitmodel", "fft_bitmodel_wide", "power_mel", "coeffs", "pipeline")}
    modules["study"] = importlib.import_module("scripts.run_fft_precision_study")
    for name, module in list(sys.modules.items()):
        if (name.startswith("software.fixed_model") or name in
                ("scripts.run_fft_precision_study", "scripts.run_fixed_pilot")):
            if not Path(module.__file__).resolve().is_relative_to(snapshot):
                raise RuntimeError("Nonfrozen imported source: " + str(module.__file__))
    return modules


def c_array(values, suffix="", width=12):
    vals = [str(int(v)) + suffix for v in np.asarray(values).ravel()]
    return "{\n" + ",\n".join("    " + ", ".join(vals[i:i+width])
                                 for i in range(0, len(vals), width)) + "\n}"


def load_published(package, out, previous):
    publication = read(package / "PUBLISHED.json")
    if publication["status"] != "PUBLISHED":
        raise ValueError("Contract publication is incomplete")
    for name, key in (("contract.json", "contract_sha256"),
                      ("artifact_hashes.json", "artifact_manifest_sha256"),
                      ("verification.json", "verification_sha256")):
        if sha(package/name) != publication[key]:
            raise ValueError("Publication hash mismatch: " + name)
    index = read(package/"artifact_hashes.json")
    for relative, expected in index.items():
        path = (package/relative).resolve()
        if not path.is_relative_to(package.resolve()) or sha(path) != expected:
            raise ValueError("Published artifact hash mismatch: " + relative)
    contract = read(package/"contract.json")
    if contract["version"] != publication["version"] or read(package/"verification.json")["status"] != "PASS":
        raise ValueError("Published version/verification inconsistent")
    required = {"n":512,"input_W":16,"input_F":15,"input_promotion_left":4,
                "internal_W":20,"internal_F":19,"output_W":20,"output_F":19,
                "twiddle_W":16,"twiddle_F":15,"stage1_W":21,"stage2_W":22,
                "physical_shift_S":9,"output_requant_shift":0}
    if any(contract["fft"].get(k) != v for k,v in required.items()):
        raise ValueError("Published FFT format unsupported; requires implementation review")
    if contract["power"]["exp2"]!="-27-2*s" or contract["mel"]["exp2"]!="-43-2*s" or \
       contract["power"]["output_unsigned_W"]!=40 or contract["mel"]["accumulator_unsigned_W"]!=60:
        raise ValueError("Published Power/Mel format unsupported")
    unchanged = {}; added = {}
    for relative,digest in contract["source_sha256"].items():
        if sha(package/"model_snapshot"/relative) != digest:
            raise ValueError("Published source hash mismatch: " + relative)
        if relative in previous["source_snapshot_sha256"]:
            if previous["source_snapshot_sha256"][relative] != digest:
                raise ValueError("Published numerical source changed; review before accepting: " + relative)
            unchanged[relative]=digest
        else: added[relative]=digest
    # Load the published package under a dedicated namespace. Legacy frozen
    # host frontend imports remain separate and cannot select a live model.
    package_name = "c_fixed_published_model"
    module_dir = package/"model_snapshot/software/fixed_model"
    spec = importlib.util.spec_from_file_location(package_name,module_dir/"__init__.py",
                                                 submodule_search_locations=[str(module_dir)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name]=module
    spec.loader.exec_module(module)
    modules={name:importlib.import_module(package_name+"."+name)
             for name in ("fft_bitmodel","fft_bitmodel_wide","power_mel","coeffs","contract")}
    vectors={name:np.fromfile(package/entry["file"],dtype=entry["dtype"]).reshape(entry["shape"])
             for name,entry in contract["vector_files"].items()}
    count=vectors["meta"].shape[0]
    if not np.array_equal(vectors["meta"][:,0],np.arange(count)):
        raise ValueError("Published frame_id order is not 0..N-1")
    if any(np.any((vectors[k]<-32768)|(vectors[k]>32767)) for k in ("input_real","input_imag")):
        raise ValueError("Published inputs exceed signed16")
    shutil.copytree(package,out/"published_contract")
    report={"passed":True,"path":str(package),"version":contract["version"],
            "publication":publication,"artifacts_checked":len(index),
            "unchanged_from_precision_snapshot":unchanged,"added_source_files":added,
            "numerical_change":"No change in FFT/qnum/Power/Mel sources or coefficients; explicit contract wrapper and published all512 directed vectors added",
            "published_frames":count}
    dump(out/"published_contract_audit.json",report)
    return contract,modules,vectors


def export_tables(frozen, out, manifest, mods, published=None):
    folder = out / "contract"
    folder.mkdir()
    table = mods["coeffs"].quantize_mel_filterbank(16)
    meta = manifest["mel_integer_dump"]
    stored = np.fromfile(frozen / "arrays" / meta["file"], dtype="<i8").reshape(meta["shape"])
    if sha(frozen / "arrays" / meta["file"]) != meta["sha256"]:
        raise ValueError("Mel stored hash mismatch")
    if not np.array_equal(stored, table.weights_int):
        raise ValueError("Original frozen generator disagrees with stored Mel codes")
    if table.manifest()["weights_int_sha256"] != manifest["mel_table"]["weights_int_sha256"]:
        raise ValueError("Mel semantic hash mismatch")
    tw_path = frozen / "source_snapshot/software/fixed_model/coefficients/twiddle_1024_w16.mem"
    if published is not None:
        published_mel=np.fromfile(published/"coefficients/mel_u32le.bin",dtype="<u4").reshape(26,257)
        if not np.array_equal(stored,published_mel):
            raise ValueError("Published Mel coefficients changed; review required")
        stored=published_mel
        tw_path=published/"coefficients/twiddle_1024_w16.mem"
    if sha(tw_path) != manifest["twiddle"]["sha256"]:
        raise ValueError("Twiddle snapshot does not match manifest")
    tw = mods["fft_bitmodel"].load_twiddle_rom(tw_path)
    (folder / "c_fixed_tables.h").write_text(
        '#ifndef C_FIXED_TABLES_H\n#define C_FIXED_TABLES_H\n#include "mfcc_fixed.h"\n'
        'extern const cf_tables c_fixed_tables;\n#endif\n', encoding="ascii")
    mel_rows = ",\n".join(c_array(row, "U") for row in stored)
    (folder / "c_fixed_tables.c").write_text(
        '/* Exact exported frozen integer coefficients; no runtime generation. */\n'
        '#include "c_fixed_tables.h"\nconst cf_tables c_fixed_tables = {\n' +
        c_array(tw[0]) + ",\n" + c_array(tw[1]) + ",\n{\n" + mel_rows + "\n}\n};\n",
        encoding="ascii")
    np.asarray(tw, dtype="<i2").tofile(folder / "twiddle_re_im_s16le.bin")
    stored.astype("<u4").tofile(folder / "mel_u32le.bin")
    shutil.copyfile(tw_path, folder / tw_path.name)
    return table, tw


def model_frame(mods, table, tw, real, imag, shift):
    fft = mods["fft_bitmodel_wide"]
    cfg = fft.FftWidthConfig(data_width=20, data_frac=19, label=CANDIDATE)
    promoted = np.asarray([real, imag], dtype=np.int64) * 16
    stages = {length: np.zeros((2, 512), dtype=np.int64) for length in (512, 128, 32, 8)}
    offsets = {length: 0 for length in stages}
    flags_by_length = {}
    original = fft._group

    def observed_group(re, im, length, tr, ti, config, flags):
        result = original(re, im, length, tr, ti, config, flags)
        start = offsets[length]
        stages[length][:, start:start+length] = result
        offsets[length] += length
        flags_by_length[length] = int(flags.overflow)
        return result

    fft._group = observed_group
    try:
        rr, ii, ov = fft.fft_fixed_natural_wide(promoted[0].tolist(), promoted[1].tolist(),
                                               *tw, cfg=cfg)
    finally:
        fft._group = original
    # The public model's natural output is an exact permutation of the final
    # trailing-stage state. Recover that state, without another algorithm.
    permutation = [fft.bit_reverse(i, 9) for i in range(512)]
    trailing = np.asarray([rr, ii], dtype=np.int64)[:, permutation]
    trace = np.stack([promoted, *[stages[k] for k in (512, 128, 32, 8)], trailing])
    power = mods["power_mel"].power_integer(rr, ii, num_bins=257, in_width=20, psum_width=40)
    mel = mods["power_mel"].mel_accumulate(power, table, accumulator_width=60)
    metadata = [int(shift), -27-2*int(shift), -43-2*int(shift), int(ov), 0, 0]
    if "contract" in mods:
        wrapper=mods["contract"].spectral_frame([int(v) for v in real],[int(v) for v in imag],
                                                int(shift),tw,table.weights_int)
        for name,expected in (("fft_real",rr),("fft_imag",ii),("power",power),("mel",mel),
                              ("bfp_s",int(shift)),("power_exp2",metadata[1]),
                              ("mel_exp2",metadata[2]),("fft_overflow",bool(ov))):
            if not np.array_equal(wrapper[name],expected):
                raise ValueError("Published wrapper differs from traced model: "+name)
    return np.concatenate([np.asarray(metadata), rr, ii, power, mel, trace.ravel(),
                           [flags_by_length[k] for k in (512,128,32,8)], [int(ov)]]).astype("<i8")


def adversarial_cases():
    rng = np.random.default_rng(20261004)
    zeros = np.zeros(512, dtype=np.int16)
    cases = []
    for value in (-32768, -32767, -1, 0, 1, 32767):
        cases.append((f"constant_{value}", np.full(512, value, dtype=np.int16), zeros.copy()))
    for position in (0, 1, 255, 256, 511):
        re, im = zeros.copy(), zeros.copy()
        re[position], im[position] = -32768, 32767
        cases.append((f"complex_impulse_{position}", re, im))
    for bin_id in (1, 3, 31, 63, 127, 255):
        phase = 2*np.pi*bin_id*np.arange(512)/512
        re = np.where(np.cos(phase) >= 0, 32767, -32768).astype(np.int16)
        im = np.where(np.sin(phase) >= 0, 32767, -32768).astype(np.int16)
        cases.append((f"complex_square_bin{bin_id}", re, im))
    for i in range(16):
        re = rng.integers(-32768, 32768, 512, dtype=np.int16)
        im = rng.integers(-32768, 32768, 512, dtype=np.int16)
        cases.append((f"random_complex_{i}", re, im))
    for i in range(8):
        re = rng.choice(np.asarray([-32768, 32767], dtype=np.int16), 512)
        im = rng.choice(np.asarray([-32768, 32767], dtype=np.int16), 512)
        cases.append((f"rail_complex_{i}", re, im))
    return [(name, re, im, [-2, 0, 24][i % 3]) for i, (name, re, im) in enumerate(cases)]


def prepare(frozen, out, project, published=None):
    manifest, audit_report = audit(frozen)
    dump(out / "frozen_audit.json", audit_report)
    mods = frozen_modules(frozen)
    published_contract=published_vectors=None
    if published is not None:
        published_contract,published_modules,published_vectors=load_published(published,out,manifest)
        mods.update(published_modules)
    table, tw = export_tables(frozen, out, manifest, mods, published)
    source = out / "frozen_snapshot"
    shutil.copytree(frozen / "source_snapshot", source)
    coeff = np.load(frozen / "arrays" / manifest["frozen_coefficient_dump"]["file"], allow_pickle=False)
    # All used original coefficients are frozen locally as an output artifact.
    shutil.copyfile(frozen / "arrays" / manifest["frozen_coefficient_dump"]["file"],
                    out / "contract/frozen_float64_coefficients.npz")
    cases, expected, records = [], [], []
    audit_matches = {name: 0 for name in ("fft_re_257", "fft_im_257", "power_257", "mel_26", "input_512", "exponents")}
    for case in manifest["part4_width_results"]:
        row = case["targets"][TARGET][CANDIDATE]
        cid, nf = case["id"], case["frames"]
        arrays = {name: np.fromfile(frozen / "arrays" / entry["file"], dtype=entry["format"]).reshape(entry["shape"])
                  for name, entry in row["integer_dumps"].items()}
        input_info = next(i for i in manifest["inputs"] if i["id"] == cid)
        pcm = np.fromfile(frozen / "arrays" / input_info["pcm"]["file"], dtype="<i2")
        if nf != (0 if len(pcm) < 512 else 1 + (len(pcm)-512)//160):
            raise ValueError("Frozen frame count differs from full-frame policy")
        # Recheck float64 host front-end and the frozen BFP/quantizer against
        # stored integer input. This does not claim an integer C front-end.
        front = mods["study"].float64_frontend(pcm, coeff["window"], 512, 160, 0.95, 32768.0)
        windowed = front[4]
        start = len(records)
        reproduced_clip = mods["pipeline"].ClipReport()
        for frame in range(nf):
            real = arrays["input_q"][frame].astype(np.int16)
            imag = np.zeros(512, dtype=np.int16)
            shift = int(arrays["shift"][frame, 0])
            quant = mods["pipeline"].InputQuantConfig(width=16, frac_bits=15,
                         clamp_lo=-32767, clamp_hi=32767)
            chosen, clamped = mods["study"].frame_exponent(windowed[frame].tolist(), 0.975, quant)
            q, clip = mods["pipeline"].quantize_frame_input(windowed[frame].tolist(), chosen, quant)
            reproduced_clip.merge(clip)
            if chosen != shift or clamped or not np.array_equal(q, real):
                raise ValueError("Frozen host quantizer reproduction mismatch: " + cid)
            answer = model_frame(mods, table, tw, real, imag, shift)
            if published_vectors is not None:
                validate_published_frame(published_vectors,len(records),real,imag,answer)
            comparisons = ((answer[6:6+257], arrays["fft_re"][frame], "fft_re_257"),
                           (answer[518:518+257], arrays["fft_im"][frame], "fft_im_257"),
                           (answer[1030:1287], arrays["psum"][frame], "power_257"),
                           (answer[1287:1313], arrays["mel"][frame], "mel_26"))
            for actual, stored, label in comparisons:
                if not np.array_equal(actual, stored):
                    raise ValueError("Frozen rerun differs from original integer dump: " + cid + "/" + label)
                audit_matches[label] += actual.size
            if bool(answer[3]) != (frame in row["fft_overflow_frame_indices"]):
                raise ValueError("Frozen overflow status mismatch")
            audit_matches["input_512"] += 512
            audit_matches["exponents"] += 1
            records.append((real, imag, shift))
            expected.append(answer)
        if reproduced_clip.as_dict() != row["clipping"]:
            raise ValueError("Frozen upstream clipping metadata mismatch: " + cid)
        ref_file = frozen / "arrays" / f"{cid}_{TARGET}_A_float64_reference_stages.npz"
        shutil.copyfile(ref_file, out / "contract" / ref_file.name)
        cases.append({"id": cid, "group": case["group"], "frames": nf, "first_record": start,
                      "pcm_samples": len(pcm), "pcm_sha256": input_info["pcm"]["sha256"],
                      "frame_starts": list(range(0, max(0, len(pcm)-511), 160)),
                      "reference_file": ref_file.name,
                      "stored_candidate_metrics": row["stages"],
                      "stored_floor_regressions": row["floor_regressions"],
                      "stored_clipping": row["clipping"],
                      "stored_shift_clamped_frames": row["shift_clamped_frames"]})
        print(f"Frozen replay {cid}: {nf} frames, all 512 FFT bins", flush=True)
    original_frames = len(records)
    published_directed_frames=0
    if published_contract is not None:
        frame_map=read(published/"vectors/frame_map.json")
        for frame in range(original_frames,published_contract["total_frames"]):
            entry=frame_map[frame]
            if entry["frame_id"]!=len(records) or entry["group"]!="directed":
                raise ValueError("Published directed frame order mismatch")
            real=published_vectors["input_real"][frame].astype(np.int16)
            imag=published_vectors["input_imag"][frame].astype(np.int16)
            shift=int(published_vectors["meta"][frame,1])
            answer=model_frame(mods,table,tw,real,imag,shift)
            validate_published_frame(published_vectors,frame,real,imag,answer)
            records.append((real,imag,shift)); expected.append(answer)
            published_directed_frames+=1
            cases.append({"id":entry["input_id"],"group":"published_directed",
                          "frames":1,"first_record":frame})
    for name, real, imag, shift in adversarial_cases():
        cases.append({"id": name, "group": "integer_adversarial", "frames": 1, "first_record": len(records)})
        expected.append(model_frame(mods, table, tw, real, imag, shift))
        records.append((real, imag, shift))
    expected_array = np.asarray(expected, dtype="<i8")
    expected_array.tofile(out / "contract/expected_i64le.bin")
    with (out / "contract/input.bin").open("wb") as f:
        f.write(b"CFIN0001" + struct.pack("<I", len(records)))
        for real, imag, shift in records:
            f.write(struct.pack("<i", shift))
            f.write(np.asarray(real, dtype="<i2").tobytes())
            f.write(np.asarray(imag, dtype="<i2").tobytes())
    dump(out / "contract/cases.json", cases)
    # A complete expected frame is embedded for the ARM validation program.
    smoke = records[0]
    answer = expected[0]
    (out / "contract/c_fixed_vectors.h").write_text(
        '#ifndef C_FIXED_VECTORS_H\n#define C_FIXED_VECTORS_H\n#include "mfcc_fixed.h"\n'
        'extern const int16_t c_fixed_smoke_re[512], c_fixed_smoke_im[512];\n'
        'extern const int32_t c_fixed_smoke_bfp_shift;\nextern const cf_result c_fixed_smoke_expected;\n#endif\n', encoding="ascii")
    (out / "contract/c_fixed_vectors.c").write_text(
        '#include "c_fixed_vectors.h"\nconst int16_t c_fixed_smoke_re[512] = ' + c_array(smoke[0]) + ';\n'
        'const int16_t c_fixed_smoke_im[512] = ' + c_array(smoke[1]) + ';\n'
        'const int32_t c_fixed_smoke_bfp_shift = ' + str(smoke[2]) + ';\n'
        'const cf_result c_fixed_smoke_expected = {\n'
        '.fft_re = ' + c_array(answer[6:518]) + ',\n.fft_im = ' + c_array(answer[518:1030]) + ',\n'
        '.power = ' + c_array(answer[1030:1287], 'ULL') + ',\n.mel = ' + c_array(answer[1287:1313], 'ULL') + ',\n'
        '.bfp_shift = ' + str(answer[0]) + ', .power_exp2 = ' + str(answer[1]) + ', .mel_exp2 = ' + str(answer[2]) + ',\n'
        '.fft_overflow = ' + str(answer[3]) + ', .power_overflow = 0, .mel_overflow = 0\n};\n', encoding="ascii")
    version=published_contract["version"] if published_contract is not None else VERSION
    contract = {"version": version, "status": "published_partial_contract" if published_contract else "provisional_partial_contract",
                "published_contract_sha256":sha(published/"contract.json") if published else None,
                "published_source_sha256":published_contract["source_sha256"] if published_contract else None,
                "published_artifact_index_sha256":sha(published/"artifact_hashes.json") if published else None,
                "source_run": str(frozen), "candidate": TARGET + "_" + CANDIDATE,
                "frozen_manifest_sha256": audit_report["manifest_sha256"],
                "frozen_artifact_index_sha256": audit_report["artifact_index_sha256"],
                "source_snapshot_sha256": manifest["source_snapshot_sha256"],
                "spec_sha256": sha(frozen / "source_snapshot/docs/MFCC_SPEC.md"),
                "coefficients": {"twiddle_rom_sha256": manifest["twiddle"]["sha256"],
                    "mel_original_dump_sha256": manifest["mel_integer_dump"]["sha256"],
                    "mel_semantic_sha256": manifest["mel_table"]["weights_int_sha256"]},
                "integer_scope": "provided FFT input16/F15 and BFP exponent -> FFT20/F19 -> Power unsigned40 -> Mel unsigned60",
                "not_implemented": ["integer PCM/preemphasis/window", "integer BFP selection", "integer log/DCT/raw13"],
                "rtl_status": "20-bit Python contract; this port does not establish RTL validation",
                "algorithm_accuracy_acceptance": "not published; report errors without numerical pass claim",
                "smoke_vector": {"case": cases[0]["id"], "frame_id": 0, "start_sample": 0,
                                 "bfp_shift": int(smoke[2]), "pcm_sha256": cases[0]["pcm_sha256"]},
                "physical_fft_shift": 9, "input_promotion_bits": 4,
                "power_exp2": "-27-2*s", "mel_exp2": "-43-2*s",
                "records": len(records), "original_frames": original_frames,
                "published_directed_frames":published_directed_frames,
                "adversarial_frames": len(records)-original_frames-published_directed_frames,
                "values_per_record": expected_array.shape[1],
                "layout": ["bfp_shift,power_exp2,mel_exp2,fft_overflow,power_overflow,mel_overflow",
                           "fft_re[512],fft_im[512],power[257],mel[26]",
                           "trace[6][2][512]: promotion, L512,L128,L32,L8,trailingL2",
                           "overflow_after_group[5]"],
                "stored_integer_comparisons": audit_matches,
                "expected_fft_overflow_frames": int(np.sum(expected_array[:,3])),
                "original_fft_overflow_frames": int(np.sum(expected_array[:original_frames,3])),
                "published_directed_fft_overflow_frames": int(np.sum(expected_array[original_frames:original_frames+published_directed_frames,3])),
                "adversarial_fft_overflow_frames": int(np.sum(expected_array[original_frames+published_directed_frames:,3])),
                "generated_sha256": {p.name: sha(p) for p in sorted((out/"contract").iterdir()) if p.is_file()}}
    dump(out / "contract/contract.json", contract)
    digest = sha(out / "contract/contract.json")
    (out / "contract/c_fixed_contract.h").write_text(
        '#ifndef C_FIXED_CONTRACT_H\n#define C_FIXED_CONTRACT_H\n'
        '#define C_FIXED_CONTRACT_VERSION "' + version + '"\n'
        '#define C_FIXED_CONTRACT_SHA256 "' + digest + '"\n'
        '#define C_FIXED_PUBLISHED_CONTRACT_SHA256 "' + (sha(published/"contract.json") if published else '') + '"\n#endif\n', encoding="ascii")
    dump(out / "prepare.json", {"status": "prepared", "contract_version": version,
          "contract_sha256": digest, "original_frames": original_frames,
          "adversarial_frames": len(records)-original_frames-published_directed_frames,
          "published_directed_frames":published_directed_frames,"records": len(records),
          "full512_expected_generated": True, "C_executed": False})
    return contract


def validate_published_frame(vectors,frame,real,imag,answer):
    pairs=((real,vectors["input_real"][frame]),(imag,vectors["input_imag"][frame]),
           (answer[6:518],vectors["fft_real"][frame]),(answer[518:1030],vectors["fft_imag"][frame]),
           (answer[1030:1287],vectors["power"][frame]),(answer[1287:1313],vectors["mel"][frame]),
           (answer[[0,3,1,2]],vectors["meta"][frame,1:]))
    if any(not np.array_equal(actual,expected) for actual,expected in pairs):
        raise ValueError("Published complete512 frame mismatch: "+str(frame))
