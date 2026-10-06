"""Export XPM ROM words from frozen C binary32 files without recomputation.

Only coefficient/provenance files are read, never development/evaluation audio.
Each floating coefficient ROM word preserves its C source's uint32 bit pattern.
Compact Mel descriptors contain only integer bin/address metadata derived from
the exact locations of positive nonzero words; no coefficient is recalculated.
Power-of-two padding is explicit zero and is never addressed as a coefficient.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any


TABLES = {
    "window": ("window.bin", "window.mem", 512, 512, [512]),
    "mel": ("mel_filters.bin", "mel.mem", 26 * 257, 8192, [26, 257]),
    "dct_cosine": ("dct_cosine.bin", "dct_cosine.mem", 13 * 26, 512, [13, 26]),
    "dct_scale": ("dct_scale.bin", "dct_scale.mem", 13, 16, [13]),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(c_reference_root: Path | str, out: Path | str) -> dict[str, Any]:
    root = Path(c_reference_root).resolve()
    out = Path(out).resolve()
    index_path = root / "artifact_manifest.json"
    index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    read_hashes: dict[str, str] = {"artifact_manifest.json": sha(index_path)}

    def verified(relative: str) -> Path:
        path = root / relative
        actual = sha(path)
        if index.get(relative) != actual:
            raise ValueError(f"Frozen C artifact hash mismatch: {relative}")
        read_hashes[relative] = actual
        return path

    manifest = json.loads(verified("run_manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("status") not in ("passed", "completed_with_numerical_failures"):
        raise ValueError("Source C run did not complete; do not use an interrupted coefficient run")
    source_manifest = json.loads(verified("coefficients/coefficient_manifest.json").read_text(encoding="utf-8-sig"))
    contract = source_manifest.get("validated_profile_contract", {})
    expected = {"profile_id": "comparison_raw13", "sample_rate": 16000,
                "frame_length": 512, "frame_step": 160, "nfft": 512,
                "num_filters": 26, "num_ceps": 13, "pcm_divisor": 32768.0,
                "preemphasis": 0.95, "frame_policy": "full_frames_only",
                "window": "symmetric_hamming", "power_divisor": 512,
                "one_sided_double": False, "log_base": "natural",
                "log_policy": "floor", "log_floor": 1e-12,
                "dct_type": 2, "dct_norm": "ortho", "lifter": 0,
                "append_energy": False, "delta": False, "delta_delta": False}
    if any(contract.get(key) != value for key, value in expected.items()):
        raise ValueError("C coefficient profile differs from the specified raw13 contract")
    buffers: dict[str, tuple[bytes, list[int]]] = {}
    for name, (source, _, logical, _, _) in TABLES.items():
        data = verified(f"coefficients/{source}").read_bytes()
        if len(data) != logical * 4:
            raise ValueError(f"Wrong binary32 coefficient count: {source}")
        floats = struct.unpack(f"<{logical}f", data)
        if not all(math.isfinite(value) for value in floats):
            raise ValueError(f"Nonfinite coefficient: {source}")
        if name in ("window", "mel", "dct_scale") and any(value < 0.0 for value in floats):
            raise ValueError(f"Unexpected negative coefficient: {source}")
        buffers[name] = (data, list(struct.unpack(f"<{logical}I", data)))
    constants = {}
    for name in ("preemphasis", "log_floor"):
        data = verified(f"coefficients/{name}.bin").read_bytes()
        if len(data) != 4:
            raise ValueError(f"Invalid scalar coefficient: {name}")
        constants[name] = {"bits_hex": f"{struct.unpack('<I', data)[0]:08x}",
                           "binary32_value": struct.unpack("<f", data)[0]}
    if constants["preemphasis"]["bits_hex"] != "3f733333" or constants["log_floor"]["bits_hex"] != "2b8cbccc":
        raise ValueError("Scalar coefficient bits differ from the reviewed C freeze")
    mel_source_bytes, mel_words = buffers["mel"]
    compact_words = []
    descriptor_words = []
    row_descriptors = []
    original_operations = []
    for row in range(26):
        row_words = mel_words[row * 257:(row + 1) * 257]
        bins = [index for index, word in enumerate(row_words) if word != 0]
        if not bins or bins != list(range(bins[0], bins[-1] + 1)):
            raise ValueError(f"Mel row {row} is empty or its nonzero support is not contiguous")
        if any(not 0 < row_words[index] < 0x7f800000 for index in bins):
            raise ValueError(f"Mel row {row} contains a non-positive/nonfinite compact word")
        first, last, offset = bins[0], bins[-1], len(compact_words)
        length = last - first + 1
        if not (0 <= first <= last <= 256 and 0 <= offset < 459 and offset + length <= 459):
            raise ValueError(f"Mel row {row} descriptor is outside its reviewed bounds")
        descriptor_words.append(first | (last << 9) | (offset << 18))
        compact_words.extend(row_words[index] for index in bins)
        original_operations.extend((row, index, row_words[index]) for index in bins)
        row_descriptors.append({"row": row, "first_bin": first, "last_bin_inclusive": last,
                                "word_offset": offset, "nonzero_words": length})
    if len(compact_words) != 459:
        raise ValueError("The reviewed source must contain exactly 459 positive Mel words")
    out.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, (source, destination, logical, physical, shape) in TABLES.items():
        data, words = buffers[name]
        contents = "".join(f"{word:08x}\n" for word in words + [0] * (physical - logical))
        path = out / destination
        path.write_text(contents, encoding="ascii", newline="\n")
        decoded = [int(word, 16) for word in path.read_text(encoding="ascii").splitlines()]
        if struct.pack(f"<{logical}I", *decoded[:logical]) != data or any(decoded[logical:]):
            raise RuntimeError(f"ROM roundtrip changed source bits: {name}")
        records[name] = {"file": destination, "source_file": f"coefficients/{source}",
                         "logical_words": logical, "physical_words": physical,
                         "logical_shape": shape, "word_bits": 32,
                         "source_sha256": read_hashes[f"coefficients/{source}"],
                         "sha256": sha(path), "padding_word_hex": "00000000",
                         "roundtrip_bit_mismatches": 0}
    compact_path, descriptor_path = out / "mel_compact.mem", out / "mel_descriptor.mem"
    compact_path.write_text("".join(f"{word:08x}\n" for word in compact_words + [0] * (512 - 459)),
                            encoding="ascii", newline="\n")
    descriptor_path.write_text("".join(f"{word:08x}\n" for word in descriptor_words + [0] * (32 - 26)),
                               encoding="ascii", newline="\n")
    decoded_compact = [int(word, 16) for word in compact_path.read_text(encoding="ascii").splitlines()]
    decoded_descriptors = [int(word, 16) for word in descriptor_path.read_text(encoding="ascii").splitlines()]
    if len(decoded_compact) != 512 or len(decoded_descriptors) != 32:
        raise RuntimeError("Compact Mel ROM physical length mismatch")
    if any(decoded_compact[459:]) or any(decoded_descriptors[26:]):
        raise RuntimeError("Compact Mel ROM padding is not positive zero")
    scattered = [0] * (26 * 257)
    decoded_operations = []
    next_offset = 0
    for row, descriptor in enumerate(decoded_descriptors[:26]):
        first, last, offset = descriptor & 0x1ff, (descriptor >> 9) & 0x1ff, (descriptor >> 18) & 0x1ff
        length = last - first + 1
        if (descriptor >> 27 or not 0 <= first <= last <= 256 or
                offset != next_offset or offset + length > 459):
            raise RuntimeError(f"Decoded Mel descriptor {row} violates range/order/packing")
        for index in range(first, last + 1):
            word = decoded_compact[offset + index - first]
            if not 0 < word < 0x7f800000:
                raise RuntimeError(f"Decoded Mel row {row} contains a zero/non-positive word")
            scattered[row * 257 + index] = word
            decoded_operations.append((row, index, word))
        next_offset += length
    if next_offset != 459 or struct.pack("<6682I", *scattered) != mel_source_bytes:
        raise RuntimeError("Compact Mel scatter roundtrip differs from the full C source")
    if decoded_operations != original_operations:
        raise RuntimeError("Compact Mel changed the ascending nonzero multiply/add sequence")
    records["mel"]["usage"] = "Dense audit copy only; the backend uses mel_compact and mel_descriptor"
    records["mel_compact"] = {"file": compact_path.name, "source_file": "coefficients/mel_filters.bin",
        "logical_words": 459, "physical_words": 512, "logical_shape": [459], "word_bits": 32,
        "source_sha256": read_hashes["coefficients/mel_filters.bin"], "sha256": sha(compact_path),
        "padding_word_hex": "00000000", "selection": "row-major ascending positive nonzero words",
        "full_scatter_roundtrip_bit_mismatches": 0, "ordered_mac_word_mismatches": 0}
    records["mel_descriptor"] = {"file": descriptor_path.name, "source_file": "coefficients/mel_filters.bin",
        "logical_words": 26, "physical_words": 32, "logical_shape": [26], "word_bits": 32,
        "source_sha256": read_hashes["coefficients/mel_filters.bin"], "sha256": sha(descriptor_path),
        "padding_word_hex": "00000000", "word_kind": "integer bin/address descriptor",
        "fields": {"first_bin": [8, 0], "last_bin_inclusive": [17, 9], "word_offset": [26, 18],
                   "reserved_zero": [31, 27]}, "rows": row_descriptors,
        "validated_constraints": "0<=first<=last<=256; offset+last-first+1<=459; consecutive row offsets"}
    report = {"schema_version": 2, "profile_id": "comparison_raw13",
              "source_c_run": str(root), "source_c_run_status": manifest["status"],
              "source_numeric_acceptance_passed": manifest.get("numerical_acceptance_passed"),
              "conversion": "Existing binary32 coefficient bits to hexadecimal words without recomputation; Mel descriptors encode only integer bin/address metadata",
              "rom_read_latency": 2, "tables": records, "constants": constants,
              "power_scale_bits_hex": "3b000000", "mel_address": "row.word_offset + k - row.first_bin",
              "mel_dense_audit_address": "m*257+k", "mel_full_scatter_roundtrip_bit_mismatches": 0,
              "mel_ordered_mac_word_mismatches": 0,
              "dct_address": "c*26+m", "dct_operation": "ascending cosine dot then separate scale multiplication",
              "mel_nonzero_words": sum(word != 0 for word in buffers["mel"][1]),
              "evaluation_audio_read": False, "verified_source_artifacts": read_hashes,
              "generator_sha256": sha(Path(__file__).resolve())}
    (out / "coefficient_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c-reference-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = generate(args.c_reference_root, args.output_dir)
    print(json.dumps({"profile_id": result["profile_id"], "tables": len(result["tables"]),
                      "mel_nonzero_words": result["mel_nonzero_words"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
