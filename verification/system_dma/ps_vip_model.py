"""Freeze the installed PS VIP and correct its simulation-only DDR request race.

The four-port write arbiter asserts a blocking request before two #0 payload
assignments. Its same-clock DDR consumer can therefore write the previous burst.
Only its 22 request assignments become nonblocking; payload and RTL are intact.
"""
from pathlib import Path
import difflib
import hashlib
import json
import re

VENDOR = Path("C:/Xilinx/Vivado/2024.2/data/ip/xilinx/processing_system7_vip_v1_0/hdl")
SOURCE = "processing_system7_vip_v1_0_vl_rfs.sv"
PRISTINE_SHA = "36ffdfc49370ad8bab1253affd69421cfb420e809fd201e1cc924d8fdc6f75f1"
MODULE = "processing_system7_vip_v1_0_21_arb_wr_4"
CACHE = Path("C:/Xilinx/Vivado/2024.2/data/xsim/ip/processing_system7_vip_v1_0_21")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cache_inventory():
    return {p.name: sha(p.read_bytes()) for p in sorted(CACHE.iterdir()) if p.is_file()}


def corrected(data):
    if sha(data) != PRISTINE_SHA:
        raise ValueError("Installed PS VIP source differs from reviewed Vivado2024.2 model")
    matches = list(re.finditer(rb"(?m)^module " + MODULE.encode() + rb"\(.*?^endmodule", data, re.S))
    if len(matches) != 1:
        raise ValueError("Expected exactly one reviewed four-port write arbiter")
    match = matches[0]
    body, count = re.subn(rb"\bprt_req([ \t]*)=([ \t]*)", rb"prt_req\1<=\2", match.group())
    if count != 22:
        raise ValueError("Reviewed request assignment count changed")
    return data[:match.start()] + body + data[match.end():]


def stage(run):
    root = Path(run) / "simulation_model"
    root.mkdir(exist_ok=False)
    pristine = (VENDOR / SOURCE).read_bytes()
    patched = corrected(pristine)
    files = {}
    for path in sorted(VENDOR.iterdir()):
        if path.suffix not in (".v", ".sv"):
            continue
        data = path.read_bytes()
        for directory in ("pristine", "patched"):
            target = root / directory / path.name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(patched if directory == "patched" and path.name == SOURCE else data)
            files[target.relative_to(run).as_posix()] = sha(target.read_bytes())
    delta = "".join(difflib.unified_diff(pristine.decode().splitlines(True), patched.decode().splitlines(True),
                                       fromfile="pristine/" + SOURCE, tofile="patched/" + SOURCE))
    (root / "scheduling_correction.diff").write_text(delta, encoding="utf-8")
    library = root / "library"
    library.mkdir()
    initfile = root / "local_xsim.ini"
    initfile.write_text("processing_system7_vip_v1_0_21=" + library.as_posix() + "\n", encoding="utf-8")
    metadata = dict(kind="vendor_PS_VIP_with_simulation_scheduling_correction", vendor_source=str(VENDOR / SOURCE),
                    pristine_sha256=PRISTINE_SHA, patched_sha256=sha(patched), module=MODULE,
                    changed_request_assignments=22, arithmetic_or_synthesized_sources_changed=False,
                    reason="Defer request to NBA after #0 payload assignments, preventing same-edge stale DDR burst arguments",
                    library="processing_system7_vip_v1_0_21", files=files,
                    physical_library=library.as_posix(), initfile=initfile.as_posix(),
                    initfile_sha256=sha(initfile.read_bytes()), installed_cache_sha256=cache_inventory(),
                    diff_sha256=sha((root / "scheduling_correction.diff").read_bytes()))
    (root / "model.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def verify(run, metadata):
    root = Path(run) / "simulation_model"
    saved = json.loads((root / "model.json").read_text(encoding="utf-8"))
    if saved != metadata:
        raise ValueError("Simulation model manifest binding changed")
    for relative, expected in metadata["files"].items():
        if sha((Path(run) / relative).read_bytes()) != expected:
            raise ValueError("Frozen simulation model changed: " + relative)
    if corrected((root / "pristine" / SOURCE).read_bytes()) != (root / "patched" / SOURCE).read_bytes():
        raise ValueError("Simulation model has changes outside reviewed request scheduling repair")
    if sha((root / "scheduling_correction.diff").read_bytes()) != metadata["diff_sha256"]:
        raise ValueError("Simulation model diff changed")
    if sha((root / "local_xsim.ini").read_bytes()) != metadata["initfile_sha256"]:
        raise ValueError("Explicit run-local VIP library mapping changed")
    if cache_inventory() != metadata["installed_cache_sha256"]:
        raise ValueError("Installed VIP compiled cache changed during this run")
