"""Create ZYBO standalone BSP and build the unchanged C core for Cortex-A9.

No hardware connection, ELF download, bitstream, boot image or flash write.
Every invocation requires a new output directory; failed attempts are preserved.
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = ROOT.parent / "build" / "arm_platform"
TOOL = Path("C:/Xilinx/Vitis/2024.2")
CPU = ["-mcpu=cortex-a9", "-mfpu=vfpv3", "-mfloat-abi=hard"]
FLAGS = CPU + ["-std=c11", "-O2", "-g3", "-fno-fast-math", "-ffp-contract=off",
               "-fexcess-precision=standard", "-fno-tree-vectorize", "-Wall", "-Wextra", "-Werror",
               "-Wno-error=enum-compare"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run(argv: list[str | Path], log: Path, cwd: Path, *, stdin: str | None = None) -> str:
    command = list(map(str, argv))
    env = os.environ.copy()
    for name in ("CFLAGS", "CPPFLAGS", "LDFLAGS", "GCC_EXEC_PREFIX", "COMPILER_PATH", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH"):
        env.pop(name, None)
    result = subprocess.run(command, cwd=cwd, env=env, input=stdin,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace")
    log.write_text(json.dumps(command) + "\n" + result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")
    return result.stdout


def unique_file(root: Path, pattern: str) -> Path:
    paths = list(root.rglob(pattern))
    if len(paths) != 1:
        raise RuntimeError(f"Expected one {pattern} under {root}, got {paths}")
    return paths[0]


def make_linker(template: Path, destination: Path) -> None:
    text = template.read_text()
    # Keep AMD's vector/startup/exception/table/constructor ordering intact.
    pattern = r"(ps7_ddr_0\s*:\s*ORIGIN\s*=\s*)0x[0-9a-fA-F]+(\s*,\s*LENGTH\s*=\s*)0x[0-9a-fA-F]+"
    text, count = re.subn(pattern, r"\g<1>0x00100000\g<2>0x02000000", text)
    if count != 1:
        raise ValueError("Unexpected generated DDR memory declaration")
    text = re.sub(r"(_STACK_SIZE\s*=\s*DEFINED\(_STACK_SIZE\)\s*\?\s*_STACK_SIZE\s*:\s*)0x[0-9A-Fa-f]+", r"\g<1>0x10000", text)
    section = """
/* Dedicated non-overlapping DDR test reservation; never a whole-DDR test. */
.ddr_test (NOLOAD) : ALIGN(4096) {
    __ddr_test_start = .;
    KEEP(*(.ddr_test))
    __ddr_test_end = .;
} > ps7_ddr_0
"""
    index = text.rfind("}")
    if index < 0:
        raise ValueError("Generated linker has no SECTIONS block")
    text = text[:index] + section + text[index:]
    text += """
ASSERT(SIZEOF(.ddr_test) == 4096, "DDR test reservation must be 4096 bytes")
ASSERT(ADDR(.ddr_test) >= ORIGIN(ps7_ddr_0), "DDR test below application DDR")
ASSERT(ADDR(.ddr_test) + SIZEOF(.ddr_test) <= ORIGIN(ps7_ddr_0) + LENGTH(ps7_ddr_0), "DDR test exceeds application DDR")
ASSERT(_stack <= ORIGIN(ps7_ddr_0) + LENGTH(ps7_ddr_0), "Stack exceeds application DDR")
"""
    destination.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-run", type=Path, required=True)
    parser.add_argument("--run-id", default="apps_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--c-reference", type=Path, default=ROOT.parent / "build/c_reference/reproduce_01")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("run-id must be a simple new directory name")
    out = DEFAULT_BUILD / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    logs = out / "logs"
    logs.mkdir()
    manifest = {"started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "status": "building", "board_executed": False, "cpu": "ps7_cortexa9_0",
                "os": "standalone", "flags": FLAGS, "fma_contraction": "off",
                "simd_vectorization": "disabled; scalar baseline", "platform_run": str(args.platform_run.resolve())}
    try:
        platform = args.platform_run.resolve()
        xsa = platform / "design/zybo_z7_20_ps.xsa"
        pm = read_json(platform / "platform_manifest.json")
        if (pm.get("status") != "xsa_exported" or pm.get("target_part") != "xc7z020clg400-1"
                or not xsa.is_file() or sha(xsa) != pm.get("xsa_sha256")):
            raise ValueError("Expected completed official ZYBO Z7-20 platform")
        with zipfile.ZipFile(xsa) as archive:
            if any(name.endswith(".bit") for name in archive.namelist()):
                raise ValueError("PS-only XSA unexpectedly contains bitstream")
            init_names = [name for name in archive.namelist() if name.endswith("ps7_init.tcl")]
            if len(init_names) != 1:
                raise ValueError("XSA must contain exactly one PS initialization script")
            ps_init = out / "ps7_init.tcl"
            ps_init.write_bytes(archive.read(init_names[0]))
            manifest["ps7_init"] = {"path": str(ps_init), "sha256": sha(ps_init), "source_xsa_member": init_names[0]}
        manifest["xsa"] = {"path": str(xsa), "sha256": sha(xsa)}
        manifest["platform_manifest_sha256"] = sha(platform / "platform_manifest.json")
        c_reference = args.c_reference.resolve()
        freeze = read_json(c_reference / "freeze.json")
        artifact = read_json(c_reference / "artifact_manifest.json")
        if artifact.get("freeze.json") != sha(c_reference / "freeze.json"):
            raise ValueError("PC reference freeze hash mismatch")
        source = out / "source"
        (source / "core").mkdir(parents=True)
        (source / "arm").mkdir()
        (source / "coefficients").mkdir()
        manifest["source_hashes"] = {}
        for name in ("mfcc.c", "mfcc.h", "fft32.c", "fft32.h"):
            path = ROOT / "software/c" / name
            key = path.relative_to(ROOT).as_posix()
            if sha(path) != freeze["binding"]["source_hashes"][key]:
                raise ValueError(f"C baseline changed: {key}")
            shutil.copy2(path, source / "core" / name)
            manifest["source_hashes"][key] = sha(path)
        for path in sorted((ROOT / "software/arm").iterdir()):
            if path.suffix in (".c", ".h", ".tcl"):
                shutil.copy2(path, source / "arm" / path.name)
                manifest["source_hashes"][path.relative_to(ROOT).as_posix()] = sha(path)
        manifest["source_hashes"]["scripts/build_arm_apps.py"] = sha(Path(__file__))
        shutil.copy2(Path(__file__), source / "build_arm_apps.py")
        for name in ("mfcc_tables.c", "mfcc_tables.h"):
            path = c_reference / "coefficients" / name
            if sha(path) != freeze["binding"]["coefficient_hashes"][name]:
                raise ValueError(f"Coefficient mismatch: {name}")
            shutil.copy2(path, source / "coefficients" / name)
        manifest["coefficient_hashes"] = freeze["binding"]["coefficient_hashes"]
        manifest["pc_reference_freeze_sha256"] = sha(c_reference / "freeze.json")
        workspace = out / "workspace"
        run([TOOL / "bin/xsct.bat", source / "arm/create_bsp.tcl", xsa, workspace], logs / "bsp.log", out)
        bsp_candidates = [p.parent for p in workspace.rglob("xparameters.h") if (p.parent.parent / "lib/libxil.a").exists()]
        # Prefer the generated domain over exported duplicate headers/libraries.
        bsp_candidates = [p for p in bsp_candidates if "export" not in p.parts]
        if len(bsp_candidates) != 1:
            raise RuntimeError(f"Ambiguous BSP include paths: {bsp_candidates}")
        include = bsp_candidates[0]
        lib = include.parent / "lib"
        xp = (include / "xparameters.h").read_text()
        manifest["bsp"] = {"include": str(include), "lib": str(lib), "xparameters_sha256": sha(include / "xparameters.h"),
                           "libxil_sha256": sha(lib / "libxil.a")}
        if not re.search(r"XPAR_PS7_DDR_0_S_AXI_HIGHADDR\s+0x3FFFFFFF", xp, re.I):
            raise ValueError("Unexpected official PS DDR upper bound; inspect before linking")
        link = out / "arm_linker.ld"
        make_linker(workspace / "hello_template/src/lscript.ld", link)
        gcc_bin = TOOL / "gnu/aarch32/nt/gcc-arm-none-eabi/bin"
        gcc = gcc_bin / "arm-none-eabi-gcc.exe"
        manifest["toolchain"] = {"nm": str(gcc_bin / "arm-none-eabi-nm.exe"), "xsct": str(TOOL / "bin/xsct.bat")}
        provenance = out / "provenance"
        provenance.mkdir()
        shutil.copy2(TOOL / "data/embeddedsw/license.txt", provenance / "AMD_embeddedsw_license.txt")
        shutil.copy2(TOOL / "gnu/aarch32/nt/gcc-arm-none-eabi/aarch32-xilinx-eabi/usr/include/_newlib_version.h",
                     provenance / "_newlib_version.h")
        manifest["runtime_provenance"] = {"source": "installed AMD Vitis 2024.2 embeddedsw and bundled GCC/Newlib",
            "embeddedsw_license_sha256": sha(provenance / "AMD_embeddedsw_license.txt"),
            "newlib_version_header_sha256": sha(provenance / "_newlib_version.h")}
        manifest["compiler"] = {"path": str(gcc), "sha256": sha(gcc),
            "version": run([gcc, "--version"], logs / "gcc_version.txt", out),
            "multilib": run([gcc, *CPU, "-print-multi-directory"], logs / "multilib.txt", out).strip()}
        libm_path = Path(run([gcc, *CPU, "-print-file-name=libm.a"], logs / "libm_path.txt", out).strip())
        manifest["libm"] = {"path": str(libm_path), "sha256": sha(libm_path), "newlib_version": "4.4.0"}
        specs_path = out / "Xilinx.spec"
        shutil.copy2(workspace / "hello_template/src/Xilinx.spec", specs_path)
        manifest["startup_specs"] = {"path": str(specs_path), "sha256": sha(specs_path)}
        macros = run([gcc, *FLAGS, "-dM", "-E", "-x", "c", "-"], logs / "compiler_macros.txt", out, stdin="#include <float.h>\n#include <_newlib_version.h>\n")
        if "#define __FLT_EVAL_METHOD__ 0" not in macros or "#define __ARM_PCS_VFP 1" not in macros:
            raise ValueError("Unexpected evaluation method or float ABI")
        binary = out / "binaries"
        binary.mkdir()
        objects = out / "objects"
        objects.mkdir()
        includes = ["-I" + str(p) for p in (source / "arm", source / "core", source / "coefficients", include)]
        input_sources = [source / "arm/arm_support.c", source / "arm/hello_main.c", source / "arm/mfcc_main.c",
                         source / "core/fft32.c", source / "core/mfcc.c", source / "coefficients/mfcc_tables.c"]
        for path in input_sources:
            run([gcc, *FLAGS, *includes, "-fstack-usage", "-c", path, "-o", objects / (path.stem + ".o")], logs / (path.stem + "_compile.log"), out)
        manifest["binaries"] = {}
        for name, stems in (("hello_ddr", ["hello_main", "arm_support"]),
                            ("mfcc_arm", ["mfcc_main", "arm_support", "mfcc", "fft32", "mfcc_tables"])):
            elf = binary / (name + ".elf")
            run([gcc, *CPU, "-specs=" + str(specs_path), "-Wl,-T," + str(link), "-Wl,-Map," + str(binary / (name + ".map")),
                 "-Wl,--build-id=none", "-o", elf, *[objects / (stem + ".o") for stem in stems],
                 "-L" + str(lib), "-Wl,--start-group", "-lxil", "-lm", "-lc", "-lgcc", "-Wl,--end-group"],
                logs / (name + "_link.log"), out)
            for tool_name, options, suffix in (("readelf", ["-h", "-A", "-l", "-S"], "elf.txt"),
                                                ("nm", ["-n", "-S"], "symbols.txt"), ("objdump", ["-d"], "disassembly.txt"),
                                                ("size", ["-A"], "size.txt")):
                run([gcc_bin / ("arm-none-eabi-" + tool_name + ".exe"), *options, elf], binary / (name + "_" + suffix), out)
            manifest["binaries"][name] = {"path": str(elf), "sha256": sha(elf)}
        manifest["linker_sha256"] = sha(link)
        manifest["status"] = "built_not_board_verified"
        manifest["board_status"] = {key: "unverified" for key in ("hello_uart", "ddr_test", "mfcc_numerics", "timing")}
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        print(str(error), file=sys.stderr)
    manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    # IDE metadata contains active lock files and asynchronous UI logs, not build inputs.
    excluded = {".metadata", ".Xil", "IDE.log", ".analytics"}
    manifest["artifact_index_excludes"] = sorted(excluded)
    write_json(out / "build_manifest.json", manifest)
    write_json(out / "artifact_manifest.json", {p.relative_to(out).as_posix(): sha(p)
               for p in sorted(out.rglob("*")) if p.is_file() and p.name != "artifact_manifest.json"
               and not any(part in excluded for part in p.relative_to(out).parts)})
    print(out)
    return 0 if manifest["status"] == "built_not_board_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
