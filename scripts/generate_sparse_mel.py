#!/usr/bin/env python3
"""Generate and audit sparse Mel RTL data from the published integer contract.

No floating point arithmetic or coefficient regeneration is used.  --check is
read-only for --output-dir.  --run-dir preserves a new audit/snapshot directory
and refuses to reuse an existing run.  All authored outputs use UTF-8 and LF.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT.parent / "build/fixed_contract/v2_pcm16_mfcc40_20261004_r2"
DEFAULT_OUTPUT = ROOT / "hardware/fixed/power_mel"
BANDS, BINS, TERMS, ROM_DEPTH, DENSE_DEPTH = 26, 257, 459, 512, 8192
PINNED_CONTRACT_SHA256 = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    return json.loads(path.read_bytes())


def member(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "contract member escapes directory")
    return path


def pinned_bytes(root, desc, hashes):
    path = member(root, desc["file"])
    data = path.read_bytes()
    require(sha(data) == desc["sha256"] == hashes[desc["file"]], "artifact hash: " + desc["file"])
    require(len(data) == desc["bytes"], "artifact byte count: " + desc["file"])
    return data


def load_coefficients(contract_dir, dense_rom):
    contract_data = (contract_dir / "contract.json").read_bytes()
    publication = read_json(contract_dir / "PUBLISHED.json")
    hash_data = (contract_dir / "artifact_hashes.json").read_bytes()
    require(publication["status"] == "PUBLISHED", "unpublished contract")
    require(sha(contract_data) == publication["contract_sha256"] == PINNED_CONTRACT_SHA256,
            "published v2 contract pin mismatch")
    require(sha(hash_data) == publication["artifact_manifest_sha256"], "artifact inventory pin mismatch")
    verification_data = (contract_dir / "verification.json").read_bytes()
    require(sha(verification_data) == publication["verification_sha256"], "verification pin mismatch")
    require(json.loads(verification_data)["status"] == "PASS", "published verification is not PASS")
    contract, hashes = json.loads(contract_data), json.loads(hash_data)
    desc = contract["coefficients"]["mel_q16"]
    require(desc["shape"] == [BANDS, BINS] and desc["dtype"] == "<u4", "Mel layout mismatch")
    binary = pinned_bytes(contract_dir, desc, hashes)
    dense = list(struct.unpack("<" + "I" * (BANDS * BINS), binary))
    coefficient_json = (contract_dir / "coefficients/coefficients.json").read_bytes()
    require(sha(coefficient_json) == hashes["coefficients/coefficients.json"], "coefficient JSON hash")
    json_dense = [value for row in json.loads(coefficient_json)["mel_q16"] for value in row]
    require(json_dense == dense, "coefficient JSON/binary mismatch")
    require(all(0 <= value <= 65536 for value in dense), "Mel F16 coefficient outside [0,65536]")
    dense_data = dense_rom.read_bytes()
    dense_words = [int(word, 16) for word in dense_data.decode("ascii").split()]
    require(len(dense_words) == DENSE_DEPTH, "dense ROM depth mismatch")
    require(dense_words[:BANDS * BINS] == dense, "dense ROM active coefficient mismatch")
    require(all(value == 0 for value in dense_words[BANDS * BINS:]), "dense ROM padding is not zero")
    sources = {
        "contract.json": sha(contract_data),
        "PUBLISHED.json": sha((contract_dir / "PUBLISHED.json").read_bytes()),
        "artifact_hashes.json": sha(hash_data),
        "verification.json": sha(verification_data),
        "coefficients/mel_q16.bin": sha(binary),
        "coefficients/coefficients.json": sha(coefficient_json),
        "dense_mel_fw16.mem": sha(dense_data),
    }
    return dense, sources, contract, hashes


def compact_coefficients(dense):
    descriptors, compact = [], []
    for band in range(BANDS):
        row = dense[band * BINS:(band + 1) * BINS]
        nonzero = [index for index, value in enumerate(row) if value != 0]
        require(bool(nonzero), "empty Mel band")
        start, length = nonzero[0], len(nonzero)
        require(nonzero == list(range(start, start + length)), "noncontiguous Mel support")
        descriptors.append(dict(band=band, start_bin=start, length=length, offset=len(compact)))
        compact.extend(row[start:start + length])
    require(len(compact) == TERMS, "unexpected nonzero count")
    return descriptors, compact


def audit_sparse(dense, descriptors, compact):
    require(len(dense) == BANDS * BINS and len(descriptors) == BANDS, "sparse shape")
    reconstructed, skipped, cursor = [0] * len(dense), [], 0
    for band, desc in enumerate(descriptors):
        start, length, offset = desc["start_bin"], desc["length"], desc["offset"]
        require(desc["band"] == band and offset == cursor, "descriptor order/offset")
        require(0 <= start < BINS and 0 < length <= BINS - start, "descriptor support range")
        require(offset + length <= len(compact), "descriptor ROM range")
        for index in range(BINS):
            address = band * BINS + index
            if start <= index < start + length:
                value = compact[offset + index - start]
                require(value != 0 and value == dense[address], "included coefficient mismatch")
                reconstructed[address] = value
            else:
                require(dense[address] == 0, "skipped coefficient is nonzero")
                skipped.append(dict(band=band, bin=index, dense_address=address, value=0))
        cursor += length
    require(cursor == len(compact) == TERMS, "compact count/coverage")
    require(reconstructed == dense, "dense reconstruction mismatch")
    require(len(skipped) == BANDS * BINS - TERMS, "zero skip count")
    return skipped


def render_artifacts(descriptors, compact, source_sha256):
    lines = [
        "// Generated by scripts/generate_sparse_mel.py; do not edit.",
        "// Source: published v2 coefficients/mel_q16.bin SHA-256:",
        "// " + source_sha256,
        "// Unsigned F16 coefficients; ascending contiguous support per band.",
        "module fixed_mel_descriptor (",
        "    input  logic [4:0] i_band,",
        "    output logic [8:0] o_start_bin,",
        "    output logic [8:0] o_length,",
        "    output logic [8:0] o_offset",
        ");",
        "    always_comb begin",
        "        o_start_bin = 9'd0;",
        "        o_length = 9'd0;",
        "        o_offset = 9'd0;",
        "        case (i_band)",
    ]
    for desc in descriptors:
        lines.extend([
            f"            5'd{desc['band']}: begin",
            f"                o_start_bin = 9'd{desc['start_bin']};",
            f"                o_length = 9'd{desc['length']};",
            f"                o_offset = 9'd{desc['offset']};",
            "            end",
        ])
    lines.extend([
        "            default: begin",
        "                o_start_bin = 9'd0;",
        "                o_length = 9'd0;",
        "                o_offset = 9'd0;",
        "            end",
        "        endcase",
        "    end",
        "endmodule",
    ])
    return {
        "fixed_mel_descriptor.sv": ("\n".join(lines) + "\n").encode("ascii"),
        "mel_sparse_fw16.mem": ("".join(f"{value:05x}\n" for value in
                                      compact + [0] * (ROM_DEPTH - len(compact)))) .encode("ascii"),
    }


def verify_corpus(contract_dir, contract, hashes, descriptors, compact):
    frames, outputs = 0, 0
    for case in contract["cases"]:
        count = case["frames"]
        power_desc, mel_desc = case["stages"]["power_u40"], case["stages"]["mel_u60"]
        require(power_desc["shape"] == [count, BINS] and power_desc["dtype"] == "<u8", "power vector shape")
        require(mel_desc["shape"] == [count, BANDS] and mel_desc["dtype"] == "<u8", "Mel vector shape")
        power = [word[0] for word in struct.iter_unpack("<Q", pinned_bytes(contract_dir, power_desc, hashes))]
        expected = [word[0] for word in struct.iter_unpack("<Q", pinned_bytes(contract_dir, mel_desc, hashes))]
        require(all(value < (1 << 40) for value in power), "corpus power width")
        for frame in range(count):
            for desc in descriptors:
                total = sum(power[frame * BINS + desc["start_bin"] + index] * compact[desc["offset"] + index]
                            for index in range(desc["length"]))
                require(total < (1 << 60), "corpus Mel overflow")
                require(total == expected[frame * BANDS + desc["band"]],
                        f"corpus mismatch {case['id']} frame={frame} band={desc['band']}")
                outputs += 1
        frames += count
    require(frames == contract["total_frames"] == 616, "corpus frame count")
    return dict(cases=len(contract["cases"]), frames=frames, mel_values=outputs, mismatches=0)


def negative_controls(dense, descriptors, compact):
    cases = []
    for name in ("shortened_length", "shifted_offset", "changed_coefficient"):
        altered_desc, altered_compact = copy.deepcopy(descriptors), compact.copy()
        if name == "shortened_length":
            altered_desc[0]["length"] -= 1
        elif name == "shifted_offset":
            altered_desc[0]["offset"] += 1
        else:
            altered_compact[0] ^= 1
        try:
            audit_sparse(dense, altered_desc, altered_compact)
        except ValueError as error:
            cases.append(dict(mutation=name, detected=True, diagnostic=str(error)))
        else:
            raise ValueError("undetected mutation: " + name)
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--dense-rom", type=Path, default=DEFAULT_OUTPUT / "mel_fw16.mem")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-dir", type=Path, help="new directory for manifest, exhaustive audit and source/artifact snapshots")
    parser.add_argument("--check", action="store_true", help="verify existing output bytes without rewriting")
    args = parser.parse_args()
    if args.run_dir:
        require(not args.run_dir.exists(), "run directory already exists")
    dense, sources, contract, hashes = load_coefficients(args.contract, args.dense_rom)
    descriptors, compact = compact_coefficients(dense)
    skipped = audit_sparse(dense, descriptors, compact)
    artifacts = render_artifacts(descriptors, compact, sources["coefficients/mel_q16.bin"])
    repeat_desc, repeat_compact = compact_coefficients(dense)
    require(artifacts == render_artifacts(repeat_desc, repeat_compact, sources["coefficients/mel_q16.bin"]),
            "generation is not deterministic")
    corpus = verify_corpus(args.contract, contract, hashes, descriptors, compact)
    mutations = negative_controls(dense, descriptors, compact)
    require(all(b"\r" not in data for data in artifacts.values()), "generated output contains CR")
    if args.check:
        for name, data in artifacts.items():
            require((args.output_dir / name).read_bytes() == data, "generated artifact mismatch: " + name)
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, data in artifacts.items():
            (args.output_dir / name).write_bytes(data)
    manifest = dict(
        schema="mfcc-sparse-mel-generator-1", status="PASS", mode="check" if args.check else "generate",
        contract=str(args.contract.resolve()), output_dir=str(args.output_dir.resolve()),
        generator_sha256=sha(Path(__file__).read_bytes()), source_sha256=sources,
        artifact_sha256={name: sha(data) for name, data in artifacts.items()},
        format=dict(bands=BANDS, bins=BINS, coefficient_width=17, coefficient_fraction_bits=16,
                    coefficient_signed=False, rom_depth=ROM_DEPTH, descriptor_widths=[9, 9, 9]),
        checks=dict(active_dense_entries=len(dense), nonzero_terms=len(compact), skipped_exact_zeros=len(skipped),
                    dense_padding_zeros=DENSE_DEPTH - len(dense), compact_padding_zeros=ROM_DEPTH - len(compact),
                    reconstructed_dense_bit_exact=True, generated_twice_byte_identical=True, lf_only=True,
                    skipped_zero_audit_sha256=sha(json_bytes(skipped)), negative_controls=mutations,
                    corpus=corpus),
        descriptors=descriptors,
        numerical_accuracy="NOT_ACCEPTED (unchanged; this checks integer identity only)",
    )
    if args.run_dir:
        args.run_dir.mkdir(parents=True)
        (args.run_dir / "source_snapshot").mkdir()
        (args.run_dir / "artifacts").mkdir()
        (args.run_dir / "source_snapshot/generate_sparse_mel.py").write_bytes(Path(__file__).read_bytes())
        for name, data in artifacts.items():
            (args.run_dir / "artifacts" / name).write_bytes(data)
        (args.run_dir / "skipped_zero_audit.json").write_bytes(json_bytes(skipped))
        (args.run_dir / "run_manifest.json").write_bytes(json_bytes(manifest))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print("FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
