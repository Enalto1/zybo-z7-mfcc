"""Recover generated DMA-system Vivado workers after a confirmed WSH dispatch failure.

Run alongside the existing system runner. Leaves the parent's project, routing,
timing and final artifact gates authoritative. No physical board access.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def wait_for(condition, description, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-run", required=True)
    parser.add_argument("--attempt-prefix", required=True)
    args = parser.parse_args()
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (args.system_run, args.attempt_prefix)):
        parser.error("Simple names required")
    system = ROOT.parent / "build/system_dma" / args.system_run
    runs = system / "design/vivado_project/zybo_dma.runs"
    helper = ROOT / "scripts/run_vivado_generated_worker.py"
    receipt = system / "worker_recovery" / (args.attempt_prefix + "_orchestration")
    receipt.mkdir(parents=True, exist_ok=False)
    (receipt / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (receipt / helper.name).write_bytes(helper.read_bytes())
    events = []

    def execute(worker, phase, previous=None):
        command = [sys.executable, "-B", str(helper), "--system-run", args.system_run,
                   "--worker", worker, "--attempt", args.attempt_prefix + "_" + phase,
                   "--allow-empty-begin"]
        if previous is not None:
            command += ["--previous-attempt", previous]
        result = subprocess.run(command, check=False)
        if result.returncode:
            raise RuntimeError(f"Worker {worker} actual exit {result.returncode}; preserved, no rerun")
        return dict(worker=worker, phase=phase, command=command, exit_code=result.returncode)

    try:
        workers = wait_for(lambda: sorted(runs.glob("*_synth_1")) if len(list(runs.glob("*_synth_1"))) == 8 else None,
                           "Eight generated OOC workers did not appear")
        if any((worker / ".vivado.end.rst").exists() or (worker / ".vivado.error.rst").exists() for worker in workers):
            raise ValueError("Automatic dispatcher requires fresh OOC workers; use individually reviewed recovery for partial runs")
        # A bounded pool is essential: queued top synthesis is not yet ready.
        with ThreadPoolExecutor(max_workers=2) as pool:
            remaining = iter(workers)
            pending = {pool.submit(execute, worker.name, worker.name) for worker in [next(remaining), next(remaining)]}
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                # Consume all finished results before dispatching another worker.
                # An error exits here; the other already-running worker is awaited.
                events.extend(future.result() for future in done)
                for _ in done:
                    worker = next(remaining, None)
                    if worker is not None:
                        pending.add(pool.submit(execute, worker.name, worker.name))
        events.append(execute("synth_1", "top_synth"))
        impl = runs / "impl_1"
        wait_for(lambda: (impl / "rundef.js").is_file() and bool(list(impl.glob(".*.queue.rst"))),
                 "Parent did not queue route implementation")
        recipe = impl / "zybo_dma_wrapper.tcl"
        route_hash = hashlib.sha256(recipe.read_bytes()).hexdigest()
        events.append(execute("impl_1", "route"))

        def bitstream_ready():
            timing_file = system / "reports/timing.json"
            if not timing_file.exists():
                return False
            timing = json.loads(timing_file.read_text())
            if timing["setup_slack_ns"] < 0 or timing["hold_slack_ns"] < 0:
                raise RuntimeError("Parent reports failed timing; stop before any bitstream recovery")
            return (recipe.is_file() and hashlib.sha256(recipe.read_bytes()).hexdigest() != route_hash
                    and (impl / ".write_bitstream.begin.rst").is_file()
                    and not (impl / ".write_bitstream.end.rst").exists()
                    and bool(list(impl.glob(".*.queue.rst"))))

        wait_for(bitstream_ready, "Parent did not generate a fresh queued bitstream recipe")
        events.append(execute("impl_1", "bitstream", args.attempt_prefix + "_route"))
        status = "COMPLETE_WORKERS_PARENT_VALIDATION_PENDING"
        error = None
        result = 0
    except Exception as exc:
        status = "FAILED"
        error = str(exc)
        result = 1
    (receipt / "orchestration.json").write_text(json.dumps(dict(status=status, error=error, events=events), indent=2) + "\n")
    print(json.dumps(dict(status=status, error=error)))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
