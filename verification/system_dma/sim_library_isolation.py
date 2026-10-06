"""Keep every generated XSim compilation target in a fresh run-local library.

Called only after Vivado scripts-only export, before any design compilation.
Vendor caches are read and cloned; they are never opened for modification.
The separately reviewed, patched PS VIP physical library is never cloned.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil

CACHE = Path("C:/Xilinx/Vivado/2024.2/data/xsim/ip")
PS = "processing_system7_vip_v1_0_21"
SIM = "design/vivado_project/zybo_dma.sim/sim_1/behav/xsim"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def targets(run):
    root = Path(run) / SIM
    projects = [root / "tb_dma_system_vlog.prj", root / "tb_dma_system_vhdl.prj"]
    libraries = set()
    for path in projects:
        text = path.read_text(encoding="utf-8-sig")
        libraries.update(re.findall(r"^(?:sv|verilog|vhdl)\s+([A-Za-z][A-Za-z0-9_]*)\s", text, re.M))
    if not libraries or "xil_defaultlib" not in libraries:
        raise ValueError("Missing generated XSim compilation targets")
    return sorted(libraries), {p.relative_to(run).as_posix(): sha(p) for p in projects}


def prepare(run):
    root = Path(run) / "simulation_model"
    target = root / "all_libraries_xsim.ini"
    if target.exists():
        raise ValueError("Refusing to replace a simulation library mapping")
    original = (root / "local_xsim.ini").read_text(encoding="utf-8")
    expected = PS + "=" + (root / "library").as_posix() + "\n"
    if original != expected:
        raise ValueError("Patched PS VIP mapping differs from exact run-local target")
    target.write_text(original, encoding="utf-8")


def stage(run):
    run = Path(run)
    root = run / "simulation_model"
    target = root / "all_libraries_xsim.ini"
    ps_mapping = PS + "=" + (root / "library").as_posix() + "\n"
    if target.read_text(encoding="utf-8") != ps_mapping or (root / "compile_library_isolation.json").exists():
        raise ValueError("Library isolation can be finalized only once from its PS-only seed")
    libraries, projects = targets(run)
    mappings = {PS: (root / "library").as_posix()}
    caches = {}
    for library in libraries:
        if library == PS:
            continue
        destination = root / "compile_libraries" / library
        destination.mkdir(parents=True, exist_ok=False)
        source = CACHE / library
        if source.is_dir():
            before = inventory(source)
            for relative, digest in before.items():
                src = source / relative
                dst = destination / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                if sha(dst) != digest:
                    raise ValueError("Vendor cache clone changed bytes")
            if inventory(source) != before:
                raise ValueError("Installed vendor cache changed during read-only clone")
            caches[library] = dict(source=source.as_posix(), files=before)
        elif library != "xil_defaultlib":
            raise ValueError("Unreviewed nonlocal library without vendor cache: " + library)
        mappings[library] = destination.as_posix()
    text = "".join(name + "=" + path + "\n" for name, path in sorted(mappings.items()))
    target.write_text(text, encoding="utf-8")
    report = dict(kind="all_compile_targets_run_local", targets=libraries, mappings=mappings,
                  initfile=target.as_posix(), initfile_sha256=sha(target), project_files=projects,
                  installed_caches=caches, shared_cache_writes_authorized=False,
                  ps_library_preserved_not_cloned=True)
    (root / "compile_library_isolation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    verify(run)
    return report


def verify(run):
    run = Path(run)
    root = run / "simulation_model"
    report = json.loads((root / "compile_library_isolation.json").read_text(encoding="utf-8"))
    target = root / "all_libraries_xsim.ini"
    if report["initfile"] != target.as_posix() or sha(target) != report["initfile_sha256"]:
        raise ValueError("All-library mapping changed")
    libraries, projects = targets(run)
    if libraries != report["targets"] or projects != report["project_files"]:
        raise ValueError("Generated compilation targets changed after library isolation")
    mappings = report["mappings"]
    if mappings.get(PS) != (root / "library").as_posix() or PS in report["installed_caches"]:
        raise ValueError("Patched PS VIP library was replaced or cloned from public cache")
    for library in libraries:
        physical = Path(mappings.get(library, "")).resolve()
        if not physical.is_relative_to(run.resolve()) or not physical.is_dir():
            raise ValueError("Compilation target resolves outside the run: " + library)
    for library, cache in report["installed_caches"].items():
        if cache["source"] != (CACHE / library).as_posix() or inventory(Path(cache["source"])) != cache["files"]:
            raise ValueError("Installed compilation cache changed: " + library)
    sim = run / SIM
    compile_text = (sim / "compile.bat").read_text(encoding="utf-8").replace("\\", "/")
    elaborate_text = (sim / "elaborate.bat").read_text(encoding="utf-8").replace("\\", "/")
    for command, text in (("xvlog", compile_text), ("xvhdl", compile_text), ("xelab", elaborate_text)):
        lines = [line for line in text.splitlines() if re.match(r"\s*call\s+" + command + r"\s", line, re.I)]
        if len(lines) != 1 or "--initfile " + target.as_posix() not in lines[0]:
            raise ValueError("Explicit all-library initfile absent from " + command)
    return report
