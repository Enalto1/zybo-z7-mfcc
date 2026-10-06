"""Run one Vivado-generated worker recipe directly when WSH cannot start.

Does not change the recipe, tool installation or registry. A genuine successful
process exit is required before writing the same completion marker as ISEWrap.
Explicit dependency guards supplement the parent Vivado final run gates.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT.parent / "build/system_dma"
VIVADO = Path("C:/Xilinx/Vivado/2024.2/bin/vivado.bat")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def prior_process_running(pid, observation):
    # Numeric Windows PIDs can be reused immediately after a completed worker.
    query = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue).ProcessName"],
                           capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    name = query.stdout.strip()
    if query.returncode != 0 or query.stderr.strip() or (name and not re.fullmatch(r"[A-Za-z0-9_.-]+", name)):
        raise OSError("Cannot identify prior worker PID")
    observation.update(pid=pid, observed_process_name=name or None)
    if name.lower() not in ("cmd", "vivado"):
        observation["prior_worker_exited"] = True
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # PID no longer exists
            observation["prior_worker_exited"] = True
            return False
        raise OSError("Cannot establish prior worker process exit")
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError("Cannot query prior worker exit")
        observation["prior_worker_exited"] = code.value != 259
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


def check_dependencies(runs, worker):
    if worker == "synth_1":
        dependencies = sorted(path for path in runs.glob("*_synth_1") if path.is_dir())
        if len(dependencies) != 8:
            raise ValueError("Expected exactly eight generated OOC synthesis dependencies")
    elif worker == "impl_1":
        dependencies = [runs / "synth_1"]
    elif worker.endswith("_synth_1"):
        dependencies = []
    else:
        raise ValueError("Unreviewed worker type")
    receipts = []
    for dep in dependencies:
        checkpoints = sorted(path for path in dep.glob("*.dcp") if path.stat().st_size > 0)
        if (not (dep / ".vivado.end.rst").is_file() or (dep / ".vivado.error.rst").exists()
                or not (dep / "__synthesis_is_complete__").is_file() or not checkpoints):
            raise ValueError(f"Dependency not successfully complete: {dep.name}")
        receipts.append(dict(worker=dep.name, checkpoints={path.name: sha(path) for path in checkpoints}))
    return receipts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--system-run", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--allow-empty-begin", action="store_true",
                   help="Recover the confirmed blocked dispatch placeholder only if it is zero bytes and no worker log exists")
    p.add_argument("--previous-attempt", help="Verified successful route receipt for a fresh parent-generated bitstream phase only")
    a = p.parse_args()
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (a.system_run, a.worker, a.attempt)):
        p.error("Simple system/worker/attempt names required")
    system = (BUILD / a.system_run).resolve()
    runs = system / "design/vivado_project/zybo_dma.runs"
    work = (runs / a.worker).resolve()
    if not work.is_relative_to(runs.resolve()) or not work.is_dir():
        p.error("Worker must be an existing generated directory in this system run")
    definition = work / "rundef.js"
    text = definition.read_text(encoding="utf-8-sig")
    matches = re.findall(r'ISEStep\(\s*"vivado",\s*"([^"\r\n]+)"\s*\);', text)
    if len(matches) != 1:
        raise ValueError("Exactly one trusted generated Vivado worker recipe required")
    args = shlex.split(matches[0])
    expected = ["-log", None, "-m64", "-product", "Vivado", "-mode", "batch", "-messageDb", "vivado.pb", "-notrace", "-source", None]
    if a.worker == "impl_1":
        expected = ["-log", None, "-applog", "-m64", "-product", "Vivado", "-messageDb", "vivado.pb", "-mode", "batch", "-source", None, "-notrace"]
    if len(args) != len(expected) or any(want is not None and args[i] != want for i, want in enumerate(expected)):
        raise ValueError("Generated worker command shape differs from reviewed Vivado2024.2 recipe")
    recipe_name = args[args.index("-source") + 1]
    if not re.fullmatch(r"[A-Za-z0-9_]+\.vd[si]", args[1]) or not re.fullmatch(r"[A-Za-z0-9_]+\.tcl", recipe_name):
        raise ValueError("Generated worker log/source must be local simple basenames")
    recipe = work / recipe_name
    if (work / ".stop.rst").exists():
        raise ValueError("Parent stop marker forbids worker launch")
    dependencies = check_dependencies(runs, a.worker)
    previous = None
    previous_begin = False
    prior_process_observation = {}
    if a.previous_attempt:
        if a.worker != "impl_1" or not re.fullmatch(r"[A-Za-z0-9_-]+", a.previous_attempt):
            raise ValueError("Previous attempt is allowed only for implementation continuation")
        prior_dir = system / "worker_recovery" / a.previous_attempt
        prior = json.loads((prior_dir / "worker.json").read_text())
        if (prior.get("status") != "COMPLETE" or prior.get("exit_code") != 0
                or prior.get("worker") != a.worker or Path(prior["directory"]).resolve() != work
                or not prior.get("generated_recipe_unchanged")
                or sha(prior_dir / "rundef.js") != prior["rundef_sha256"]
                or sha(prior_dir / recipe.name) != prior["recipe_sha256"]
                or sha(prior_dir / "executed_helper.py") != prior["script_sha256"]
                or sha(prior_dir / "console.log") != prior["console_sha256"]
                or sha(prior_dir / ("worker_" + args[1])) != prior.get("worker_log_sha256")
                or sha(work / args[1]) != prior.get("worker_log_sha256")
                or sha(recipe) == prior["recipe_sha256"]):
            raise ValueError("Previous route receipt/log/recipe does not authenticate this continuation")
        timing = json.loads((system / "reports/timing.json").read_text())
        if timing["setup_slack_ns"] < 0 or timing["hold_slack_ns"] < 0:
            raise ValueError("Failed route timing forbids bitstream continuation")
        completed = datetime.fromisoformat(prior["completed_at_utc"]).timestamp()
        bit_begin = work / ".write_bitstream.begin.rst"
        if (not bit_begin.is_file() or bit_begin.stat().st_size != 0 or bit_begin.stat().st_mtime < completed
                or recipe.stat().st_mtime < completed or definition.stat().st_mtime < completed
                or re.findall(r"^start_step\s+(\w+)", recipe.read_text(), re.M) != ["write_bitstream"]
                or not re.search(r'ISETouchFile\(\s*"write_bitstream",\s*"begin"\s*\)', text)
                or (work / ".write_bitstream.end.rst").exists() or (work / ".write_bitstream.error.rst").exists()
                or prior_process_running(prior["pid"], prior_process_observation)):
            raise ValueError("Parent has not freshly queued only write_bitstream, or prior worker is still live")
        old_begin = work / ".vivado.begin.rst"
        if old_begin.exists() and old_begin.stat().st_size:
            previous_begin = ET.parse(old_begin).getroot().find("Process").get("Pid") == str(prior["pid"])
            if not previous_begin or old_begin.stat().st_mtime > completed:
                raise ValueError("Retained generic begin does not match prior successful worker")
        old_end = work / ".vivado.end.rst"
        if old_end.exists() and (old_end.stat().st_size or not completed - 2 <= old_end.stat().st_mtime <= completed + 2):
            raise ValueError("Retained generic end does not match prior successful worker")
        previous = dict(attempt=a.previous_attempt, receipt_sha256=sha(prior_dir / "worker.json"),
                        prior_recipe_sha256=prior["recipe_sha256"], prior_worker_log_sha256=prior["worker_log_sha256"],
                        prior_process_observation=prior_process_observation)
    begin = work / ".vivado.begin.rst"
    empty_placeholder = begin.exists() and begin.stat().st_size == 0 and (previous is not None or (not (work / args[1]).exists() and not (work / "runme.log").exists()))
    if not recipe.is_file() or (begin.exists() and not (previous_begin or (a.allow_empty_begin and empty_placeholder))) or ((work / ".vivado.end.rst").exists() and previous is None):
        raise ValueError("Worker missing or already started/completed; do not race another worker")
    if not list(work.glob(".*.queue.rst")):
        raise ValueError("Parent has not queued this worker")
    evidence = system / "worker_recovery" / a.attempt
    evidence.mkdir(parents=True, exist_ok=False)
    original = dict(rundef_sha256=sha(definition), recipe_sha256=sha(recipe))
    if empty_placeholder:
        (evidence / "blocked_dispatch_empty_begin.rst").write_bytes(begin.read_bytes())
    if previous is not None:
        (evidence / ("prior_worker_" + args[1])).write_bytes((work / args[1]).read_bytes())
        if (work / "runme.log").is_file():
            (evidence / "prior_runme.log").write_bytes((work / "runme.log").read_bytes())
        for name in (".vivado.begin.rst", ".vivado.end.rst", ".write_bitstream.begin.rst"):
            if (work / name).exists():
                (evidence / ("prior_" + name)).write_bytes((work / name).read_bytes())
    (evidence / "rundef.js").write_bytes(definition.read_bytes())
    (evidence / recipe.name).write_bytes(recipe.read_bytes())
    (evidence / "executed_helper.py").write_bytes(Path(__file__).read_bytes())
    command = [str(VIVADO), *args]
    metadata = dict(status="STARTING", kind="direct_generated_recipe_replaces_blocked_WSH_wrapper",
                    system_run=a.system_run, worker=a.worker, directory=str(work), command=command,
                    started_at_utc=datetime.now(timezone.utc).isoformat(), script_sha256=sha(__file__), **original)
    metadata["blocked_empty_begin_placeholder_preserved"] = empty_placeholder
    metadata["verified_dependencies"] = dependencies
    metadata["previous_phase"] = previous
    dump(evidence / "worker.json", metadata)
    child = None
    code = None
    unchanged = False
    exception = None
    try:
        with (evidence / "console.log").open("w", encoding="utf-8") as log:
            if (work / ".stop.rst").exists():
                raise ValueError("Parent stop marker appeared before launch")
            child = subprocess.Popen(command, cwd=work, stdout=log, stderr=subprocess.STDOUT)
            metadata["pid"] = child.pid
            marker = ET.Element("ProcessHandle", Version="1", Minor="0")
            ET.SubElement(marker, "Process", Command="vivado", Owner=os.environ.get("USERNAME", ""),
                          Host=socket.gethostname(), Pid=str(child.pid), HostCore=str(os.cpu_count()))
            ET.ElementTree(marker).write(work / ".vivado.begin.rst", encoding="utf-8", xml_declaration=True)
            dump(evidence / "worker.json", metadata)
            code = child.wait()
        unchanged = original == dict(rundef_sha256=sha(definition), recipe_sha256=sha(recipe))
    except BaseException as error:
        exception = f"{type(error).__name__}: {error}"
    finally:
        # Never orphan an already launched worker or report its completion early.
        if child is not None and code is None:
            while child.poll() is None:
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    pass
            code = child.returncode
        stopped = (work / ".stop.rst").exists()
        phase_passed = previous is None or ((work / ".write_bitstream.end.rst").is_file() and not (work / ".write_bitstream.error.rst").exists())
        metadata.update(exit_code=code, completed_at_utc=datetime.now(timezone.utc).isoformat(),
                        generated_recipe_unchanged=unchanged, exception=exception,
                        parent_stop_marker=stopped, requested_phase_completed=phase_passed)
        metadata["status"] = "COMPLETE" if child is not None and code == 0 and unchanged and exception is None and not stopped and phase_passed else "FAILED"
        marker = ".vivado.end.rst" if metadata["status"] == "COMPLETE" else ".vivado.error.rst"
        try:
            if (evidence / "console.log").exists():
                metadata["console_sha256"] = sha(evidence / "console.log")
            if (work / args[1]).is_file():
                (evidence / ("worker_" + args[1])).write_bytes((work / args[1]).read_bytes())
                metadata["worker_log_sha256"] = sha(evidence / ("worker_" + args[1]))
            dump(evidence / "worker.json", metadata)
        finally:
            (work / marker).write_bytes(b"")
    print(json.dumps(metadata, indent=2))
    return 0 if metadata["status"] == "COMPLETE" else code or 1


if __name__ == "__main__":
    raise SystemExit(main())
