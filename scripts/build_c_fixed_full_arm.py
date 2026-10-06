"""Cross-build full fixed PCM/raw13 adapters; never connect to a board.

Reuses a completed, hash-verified PS-only ZYBO Z7-20 XSA/standalone BSP. All
outputs and copied inputs live in a new child directory of a c_fixed run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / "build"
VITIS = Path("C:/Xilinx/Vitis/2024.2")
PUBLISHED_CONTRACT_SHA256 = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"
CPU = ["-mcpu=cortex-a9", "-mfpu=vfpv3", "-mfloat-abi=hard"]
FLAGS = CPU + ["-std=c11", "-O2", "-g3", "-fno-tree-vectorize", "-fno-lto",
               "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wsign-conversion",
               "-fstack-usage"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args, log: Path, cwd: Path, stdin=None) -> str:
    env = os.environ.copy()
    for key in ("CFLAGS", "CPPFLAGS", "LDFLAGS", "GCC_EXEC_PREFIX", "COMPILER_PATH",
                "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH"):
        env.pop(key, None)
    command = list(map(str, args))
    p = subprocess.run(command, cwd=cwd, env=env, input=stdin, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace")
    log.write_text(json.dumps(command) + "\n" + p.stdout, encoding="utf-8")
    if p.returncode:
        raise RuntimeError(f"Command returned {p.returncode}; see {log}")
    return p.stdout


def copy_bound(path: Path, dest: Path, index: dict, base: Path) -> dict:
    key = path.relative_to(base).as_posix()
    actual = sha(path)
    if index.get(key) != actual:
        raise ValueError(f"Frozen artifact hash mismatch or missing: {path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    if sha(dest) != actual:
        raise ValueError(f"Snapshot changed while copying: {path}")
    return {"source": str(path), "sha256": actual}


def make_linker(template: Path, dest: Path) -> None:
    value = template.read_text(encoding="utf-8")
    value, n = re.subn(r"(ps7_ddr_0\s*:\s*ORIGIN\s*=\s*)0x[0-9A-Fa-f]+(\s*,\s*LENGTH\s*=\s*)0x[0-9A-Fa-f]+",
                       r"\g<1>0x00100000\g<2>0x02000000", value)
    if n != 1:
        raise ValueError("Unexpected generated linker DDR declaration")
    value, n = re.subn(r"(_STACK_SIZE\s*=\s*DEFINED\(_STACK_SIZE\)\s*\?\s*_STACK_SIZE\s*:\s*)0x[0-9A-Fa-f]+",
                       r"\g<1>0x10000", value)
    if n != 1:
        raise ValueError("Unexpected generated stack declaration")
    value += '\nASSERT(_stack <= ORIGIN(ps7_ddr_0) + LENGTH(ps7_ddr_0), "Stack exceeds fixed app DDR")\n'
    dest.write_text(value, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--arm-id", default="arm_full_01")
    parser.add_argument("--bsp-run", type=Path, default=BASE / "arm_platform/reproduce_01_apps")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir() or run_dir.parent != BASE.resolve() or not run_dir.name.startswith("c_fixed_v2_"):
        parser.error("run-dir must be an existing dedicated build/c_fixed* run")
    if not re.fullmatch(r"arm_[A-Za-z0-9_-]+", args.arm_id):
        parser.error("arm-id must start arm_ and contain simple filename characters")
    out = run_dir / args.arm_id
    out.mkdir(exist_ok=False)
    logs, source, binary, objects = (out / x for x in ("logs", "source", "binaries", "objects"))
    for path in (logs, source, binary, objects):
        path.mkdir()
    manifest = {"started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "status": "building", "scope": "full PCM16->raw13 fixed MFCC",
                "board_executed": False, "arm_bit_comparison_executed": False,
                "timing_measured": False, "flags": FLAGS,
                "core_extra_flags": ["-mgeneral-regs-only"],
                "core_register_policy": "integer core uses only general registers; avoid GCC VFP registers for integer constant moves",
                "optimization": "scalar O2, no automatic vectorization, no LTO",
                "board": "ZYBO Z7-20", "cpu": "ps7_cortexa9_0", "os": "standalone"}
    try:
        arm_index = read_json(run_dir / "arm_input_hashes.json")
        # The preparation/PC runner publishes this index only for immutable
        # snapshots. Never import current software/fixed_model or live C core.
        if "files" in arm_index:
            arm_index = arm_index["files"]
        for key in ("prepare.json", "pc_results.json", "source_hashes.json", "contract/contract.json", "contract/c_fixed_contract.h"):
            if arm_index.get(key) != sha(run_dir / key):
                raise ValueError("ARM input index mismatch: " + key)
        prepared = read_json(run_dir / "prepare.json")
        pc_result = read_json(run_dir / "pc_results.json")
        if pc_result.get("status") != "passed":
            raise ValueError("PC bit verification must pass before cross-build")
        contract = read_json(run_dir / "contract/contract.json")
        if contract["version"] != "v2_pcm16_mfcc40_20261004_r2":
            raise ValueError("This full ARM adapter supports only the published v2 r2 contract")
        if prepared["contract_sha256"] != sha(run_dir / "contract/contract.json"):
            raise ValueError("Prepared contract JSON hash mismatch")
        contract_header = (run_dir / "contract/c_fixed_contract.h").read_text(encoding="utf-8")
        for key, expected in (("C_FIXED_CONTRACT_VERSION", contract["version"]),
                              ("C_FIXED_CONTRACT_SHA256", prepared["contract_sha256"]),
                              ("C_FIXED_PUBLISHED_CONTRACT_SHA256", PUBLISHED_CONTRACT_SHA256)):
            match = re.search(r"^#define\s+" + key + r'\s+"([^"]+)"', contract_header, re.M)
            if match is None or match.group(1) != expected:
                raise ValueError("Embedded contract identity mismatch: " + key)
        manifest["pc_binding"] = {"arm_input_index_sha256": sha(run_dir / "arm_input_hashes.json"),
            "pc_results_sha256": sha(run_dir / "pc_results.json"), "pc_status": pc_result["status"],
            "source_hashes_sha256": sha(run_dir / "source_hashes.json"),
            "contract_version": contract["version"], "contract_sha256": prepared["contract_sha256"],
            "published_contract_sha256": PUBLISHED_CONTRACT_SHA256}
        manifest["development_vector"] = contract.get("development_vector", {})
        old = args.bsp_run.resolve()
        bm = read_json(old / "build_manifest.json")
        index = read_json(old / "artifact_manifest.json")
        if index.get("build_manifest.json") != sha(old / "build_manifest.json"):
            raise ValueError("Reused BSP build manifest hash mismatch")
        if bm["status"] != "built_not_board_verified" or bm["cpu"] != "ps7_cortexa9_0":
            raise ValueError("Expected completed Cortex-A9 standalone BSP")
        platform = Path(bm["platform_run"])
        pm = read_json(platform / "platform_manifest.json")
        xsa = Path(bm["xsa"]["path"])
        if (pm["target_part"] != "xc7z020clg400-1" or pm["board_part"] != "digilentinc.com:zybo-z7-20:part0:1.2"
                or pm["status"] != "xsa_exported" or sha(xsa) != bm["xsa"]["sha256"]
                or sha(xsa) != pm["xsa_sha256"] or sha(platform / "platform_manifest.json") != bm["platform_manifest_sha256"]):
            raise ValueError("ZYBO PS-only XSA/platform integrity mismatch")
        with zipfile.ZipFile(xsa) as archive:
            if any(name.endswith(".bit") for name in archive.namelist()):
                raise ValueError("PS-only XSA unexpectedly includes bitstream")
            hwh = [name for name in archive.namelist() if name.endswith(".hwh")]
            if len(hwh) != 1:
                raise ValueError("Expected one hardware handoff in XSA")
            modules = [{"instance": item.get("INSTANCE"), "type": item.get("MODTYPE")}
                       for item in ET.fromstring(archive.read(hwh[0])).findall(".//MODULE")]
            if len(modules) != 1 or modules[0]["type"] != "processing_system7":
                raise ValueError("Reused XSA must contain only processing_system7")
        manifest["reused_platform"] = {"xsa": str(xsa), "xsa_sha256": sha(xsa),
            "bsp_run": str(old), "bsp_manifest_sha256": sha(old / "build_manifest.json"),
            "platform_manifest_sha256": sha(platform / "platform_manifest.json"), "hwh_modules": modules}
        manifest["bsp_input_hashes"] = {}
        include, lib = Path(bm["bsp"]["include"]), Path(bm["bsp"]["lib"])
        for parent, dirname in ((include, "include"), (lib, "lib")):
            for item in sorted(parent.rglob("*")):
                if item.is_file():
                    key = dirname + "/" + item.relative_to(parent).as_posix()
                    manifest["bsp_input_hashes"][key] = copy_bound(item, out / "bsp" / key, index, old)
        include, lib = out / "bsp/include", out / "bsp/lib"
        if sha(include / "xparameters.h") != bm["bsp"]["xparameters_sha256"] or sha(lib / "libxil.a") != bm["bsp"]["libxil_sha256"]:
            raise ValueError("Copied BSP header/library mismatch")
        xp = (include / "xparameters.h").read_text()
        if not re.search(r"XPAR_PS7_DDR_0_S_AXI_HIGHADDR\s+0x3FFFFFFF", xp, re.I):
            raise ValueError("Unexpected DDR bound in BSP")
        manifest["bsp_input_hashes"]["ps7_init.tcl"] = copy_bound(old / "ps7_init.tcl", out / "ps7_init.tcl", index, old)
        manifest["bsp_input_hashes"]["Xilinx.spec"] = copy_bound(old / "Xilinx.spec", out / "Xilinx.spec", index, old)
        template = old / "workspace/hello_template/src/lscript.ld"
        manifest["bsp_input_hashes"]["original_linker.ld"] = copy_bound(template, out / "original_linker.ld", index, old)
        make_linker(out / "original_linker.ld", out / "arm_c_fixed_full_linker.ld")
        manifest["source_hashes"] = {}
        frozen_source_hashes = read_json(run_dir / "source_hashes.json")
        for dirname, parent, names in (
            ("core", run_dir / "source_snapshot/software/c_fixed", ["fixed_int.c", "fixed_int.h", "mfcc_fixed.c", "mfcc_fixed.h", "mfcc_fixed_full.c", "mfcc_fixed_full.h"]),
            ("arm", ROOT / "software/arm_fixed", ["arm_c_fixed_full.c", "arm_c_fixed_full.h", "validate_c_fixed_full_main.c", "timing_c_fixed_full_main.c"]),
            ("contract", run_dir / "contract", ["c_fixed_tables.c", "c_fixed_tables.h", "c_fixed_full_tables.c", "c_fixed_full_tables.h", "c_fixed_full_vectors.c", "c_fixed_full_vectors.h", "c_fixed_contract.h"])):
            (source / dirname).mkdir()
            for name in names:
                path = parent / name
                if dirname == "core":
                    if frozen_source_hashes.get("software/c_fixed/" + name) != sha(path):
                        raise ValueError("PC core source hash mismatch: " + name)
                if dirname == "contract" and name != "c_fixed_contract.h":
                    if contract["generated_sha256"].get(name) != sha(path):
                        raise ValueError("Generated contract artifact hash mismatch: " + name)
                if dirname != "arm":
                    record = copy_bound(path, source / dirname / name, arm_index, run_dir)
                else:
                    digest = sha(path)
                    shutil.copy2(path, source / dirname / name)
                    if sha(source / dirname / name) != digest or sha(path) != digest:
                        raise ValueError("ARM adapter changed during snapshot: " + name)
                    record = {"source": str(path), "sha256": digest}
                manifest["source_hashes"][dirname + "/" + name] = record
        dev = contract["development_vector"]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+", dev["case"])
                or not re.fullmatch(r"[a-f0-9]{64}", dev["pcm_sha256"])
                or dev["samples"] != 85920 or dev["frames"] != 534):
            raise ValueError("Unsupported development identity metadata")
        binding_header = source / "contract/arm_c_fixed_full_binding.h"
        binding_header.write_text(
            '#ifndef ARM_C_FIXED_FULL_BINDING_H\n#define ARM_C_FIXED_FULL_BINDING_H\n'
            + '#define ARM_CF_FULL_CASE "' + dev["case"] + '"\n'
            + '#define ARM_CF_FULL_PCM_SHA256 "' + dev["pcm_sha256"] + '"\n#endif\n',
            encoding="utf-8")
        manifest["generated_binding_header_sha256"] = sha(binding_header)
        builder_sha256 = sha(Path(__file__))
        shutil.copy2(__file__, source / "build_c_fixed_full_arm.py")
        if sha(source / "build_c_fixed_full_arm.py") != builder_sha256:
            raise ValueError("Builder changed while copying")
        manifest["builder_sha256"] = builder_sha256
        contract_jsons = {}
        for path in (run_dir / "contract/contract.json",):
            copy_bound(path, source / "contract" / path.name, arm_index, run_dir)
            contract_jsons[path.name] = sha(path)
        for name in ("prepare.json", "pc_results.json", "source_hashes.json"):
            copy_bound(run_dir / name, source / name, arm_index, run_dir)
        manifest["contract_metadata_hashes"] = contract_jsons
        gcc_bin = VITIS / "gnu/aarch32/nt/gcc-arm-none-eabi/bin"
        gcc = gcc_bin / "arm-none-eabi-gcc.exe"
        manifest["compiler"] = {"path": str(gcc), "sha256": sha(gcc),
            "version": run([gcc, "--version"], logs / "gcc_version.txt", out)}
        if sha(gcc) != bm["compiler"]["sha256"]:
            raise ValueError("ARM compiler changed since verified BSP build")
        manifest["runtime_libraries"] = {}
        for name in ("libc.a", "libgcc.a"):
            path = Path(run([gcc, *CPU, "-print-file-name=" + name], logs / (name + "_path.txt"), out).strip())
            manifest["runtime_libraries"][name] = {"path": str(path), "sha256": sha(path)}
        provenance = out / "provenance"
        provenance.mkdir()
        for path in (VITIS / "data/embeddedsw/license.txt",
                     VITIS / "gnu/aarch32/nt/gcc-arm-none-eabi/aarch32-xilinx-eabi/usr/include/_newlib_version.h"):
            shutil.copy2(path, provenance / path.name)
        manifest["runtime_provenance"] = {p.name: sha(p) for p in provenance.iterdir()}
        run([gcc, *FLAGS, "-dM", "-E", "-x", "c", "-"], logs / "macros.txt", out, "#include <stdint.h>\n")
        includes = ["-I" + str(p) for p in (source / "core", source / "arm", source / "contract", include)]
        sources = [source / "core/fixed_int.c", source / "core/mfcc_fixed.c", source / "core/mfcc_fixed_full.c",
                   source / "contract/c_fixed_tables.c", source / "contract/c_fixed_full_tables.c",
                   source / "contract/c_fixed_full_vectors.c", source / "arm/arm_c_fixed_full.c",
                   source / "arm/validate_c_fixed_full_main.c", source / "arm/timing_c_fixed_full_main.c"]
        for path in sources:
            core_flags = ["-mgeneral-regs-only"] if path.parent.name == "core" else []
            run([gcc, *FLAGS, *core_flags, *includes, "-c", path, "-o", objects / (path.stem + ".o")], logs / (path.stem + "_compile.log"), out)
        audit = {"objects": {}, "float_instructions_found": [], "float_helpers_found": [],
                 "scope": "fixed core and immutable tables, not BSP startup"}
        for name in ("fixed_int", "mfcc_fixed", "mfcc_fixed_full", "c_fixed_tables", "c_fixed_full_tables"):
            path = objects / (name + ".o")
            dis = run([gcc_bin / "arm-none-eabi-objdump.exe", "-d", path], objects / (name + "_disassembly.txt"), out)
            undefined = run([gcc_bin / "arm-none-eabi-nm.exe", "-u", path], objects / (name + "_undefined.txt"), out)
            # Any VFP/NEON mnemonic is flagged; this scalar integer core needs none.
            hits = [line for line in dis.splitlines() if re.search(r"^\s*[0-9a-f]+:\s+(?:[0-9a-f]+\s+)+v[a-z][a-z0-9.]*\s", line)]
            helper_hits = [line for line in undefined.splitlines() if re.search(r"__aeabi_(?:[fd]|[iu]2[fd]|l2[fd]|ul2[fd])|\b(?:logf?|expf?|powf?|sqrtf?)\b|__(?:multi3|divti3|udivti3|modti3|umodti3)", line)]
            audit["objects"][name] = {"sha256": sha(path), "vfp_neon_instruction_count": len(hits), "float_helper_count": len(helper_hits)}
            audit["float_instructions_found"].extend(hits)
            audit["float_helpers_found"].extend(helper_hits)
        write_json(out / "integer_core_audit.json", audit)
        if audit["float_instructions_found"] or audit["float_helpers_found"]:
            raise ValueError("Unexpected floating-point operations in integer core")
        manifest["binaries"] = {}
        for mode in ("validate", "timing"):
            name = "c_fixed_full_" + mode
            elf = binary / (name + ".elf")
            stems = ["fixed_int", "mfcc_fixed", "mfcc_fixed_full", "c_fixed_tables", "c_fixed_full_tables", "c_fixed_full_vectors", "arm_c_fixed_full", mode + "_c_fixed_full_main"]
            run([gcc, *CPU, "-fno-lto", "-specs=" + str(out / "Xilinx.spec"), "-Wl,-T," + str(out / "arm_c_fixed_full_linker.ld"),
                 "-Wl,-Map," + str(binary / (name + ".map")), "-Wl,--build-id=none", "-o", elf,
                 *[objects / (s + ".o") for s in stems], "-L" + str(lib),
                 "-Wl,--start-group", "-lxil", "-lc", "-lgcc", "-Wl,--end-group"], logs / (name + "_link.log"), out)
            reports = {}
            for tool, options, suffix in (("readelf", ["-h", "-A", "-l", "-S"], "elf.txt"),
                                          ("nm", ["-n", "-S"], "symbols.txt"),
                                          ("objdump", ["-d"], "disassembly.txt"), ("size", ["-A"], "size.txt")):
                reports[suffix] = run([gcc_bin / ("arm-none-eabi-" + tool + ".exe"), *options, elf], binary / (name + "_" + suffix), out)
            data_sizes = {}
            for line in reports["symbols.txt"].splitlines():
                fields = line.split()
                if len(fields) == 4 and (fields[3].startswith("arm_c_fixed_full_") or fields[3].startswith("c_fixed_full_") or fields[3] in {"c_fixed_tables", "full_context"}):
                    data_sizes[fields[3]] = {"address": "0x" + fields[0], "bytes": int(fields[1], 16)}
            manifest["binaries"][name] = {"path": str(elf), "sha256": sha(elf), "symbol_sizes": data_sizes}
        manifest["stack_usage_files"] = {p.name: sha(p) for p in sorted(objects.glob("*.su"))}
        manifest["status"] = "built_not_board_verified"
        manifest["timing_scope"] = "full development clip: reset+85920 PCM pushes+raw13/metadata output stores; context initialization, checksum/comparison, UART and cache flush outside interval; 3 warmups+30 repeats; timer read overhead recorded, not subtracted"
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        print(str(error), file=sys.stderr)
    manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(out / "build_manifest.json", manifest)
    write_json(out / "artifact_manifest.json", {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*"))
                                               if p.is_file() and p.name != "artifact_manifest.json"})
    print(out)
    return 0 if manifest["status"] == "built_not_board_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
