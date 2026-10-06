"""Collect immutable fixed optimization evidence into English CSV/JSON tables.

Read-only with respect to every input run. Creates a new output directory and
never replaces the main optimization report. Missing candidates are NOT_RUN.
Board durations are independently recomputed from the raw 168-byte trials;
all 33 raw output histories must match the existing baseline record bytes.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT.parent / "build"
BASELINE_BOARD = BUILD / "board_validation/dma_board_20261005_01/fixed_03"
PCM_SHA = "026cae2934935a27911f88d7d6e0ddcc678ad433fb50fe764233a57e4bbcde32"
RECORD_SHA = "888ee289cc44f03602934002e9c4e2793cd3c4bc6349547c247bbda934eba6a6"
CONTRACT_SHA = "283fff8abec22b3455a0219e37d27ac9c090ed2e74be3f7256677fb0cf4d6e3e"
STAT_NAMES = ("samples frames tx_bytes rx_bytes core_status core_error_flags core_error_detail input_received input_consumed "
              "output_captured output_sent input_bytes output_bytes tx_dma_status rx_dma_status rx_actual_bytes "
              "irq_tx_count irq_rx_count irq_core_count irq_timer_count irq_tx_status irq_rx_status irq_core_status events "
              "wfi_count deadline_fired recovery_attempted recovery_failed failed_offset reserved0 reserved1 reserved2").split()
EVENTS = ("frame_admit", "frontend_last", "fft_first", "fft_last", "mel_first", "mel_last", "mfcc_first", "mfcc_last")
DELTAS = (("frame_admit", "frontend_last"), ("frontend_last", "fft_first"),
          ("fft_first", "fft_last"), ("fft_last", "mel_first"), ("mel_first", "mel_last"),
          ("fft_last", "mel_last"), ("mel_last", "mfcc_first"),
          ("frame_admit", "mfcc_first"), ("frame_admit", "mfcc_last"))
NOTES = [
    "All fixed candidates retain numerical acceptance NOT_ACCEPTED; exact RTL equality is a separate result.",
    "Board whole-clip milliseconds cover cache/configuration, DMA and completion checks using the existing DMA/IRQ boundary.",
    "PL BUSY includes stalls, serialization and reset/recovery; it is not isolated compute time.",
    "WFI bracket includes wait-instruction overhead and is not a CPU-utilization measurement.",
    "Whole-clip/534 is an amortized frame average, not first-result latency or frame initiation interval.",
    "Simulation events mark accepted transactions; MFCC first-result latency includes testbench output stalls.",
    "Frame initiation intervals are differences between consecutive accepted frame_admit events in one case.",
    "Tail intervals exclude the requested number of leading frame IDs; they remain observed TB intervals, not proof of unstalled throughput.",
    "Stage edge deltas exclude an inclusive-cycle +1 adjustment. Simulation clock conversion uses the run's recorded clock_ns.",
    "Resource totals include the common PS/DMA/interconnect system and are taken only from post-route reports.",
]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    point = (len(ordered) - 1) * 0.95
    low = int(point)
    return dict(count=len(values), min=min(values), median=statistics.median(values),
                mean=statistics.mean(values), max=max(values),
                p95=ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (point - low))


class Evidence:
    def __init__(self, root, role, collected):
        self.root = Path(root).resolve()
        self.role = role
        self.collected = collected
        index_path = self.root / "artifact_hashes.json"
        self.index = json.loads(index_path.read_text(encoding="utf-8-sig")) if index_path.is_file() else None
        if self.index is not None:
            self._record(index_path, "artifact_hashes.json", True)

    def _record(self, path, name, indexed):
        digest = sha(path)
        if self.index is not None and name != "artifact_hashes.json":
            require(self.index.get(name) == digest, f"{self.role}: changed/unindexed evidence {name}")
        self.collected.append(dict(role=self.role, path=str(path), relative_path=name,
                                   bytes=path.stat().st_size, sha256=digest, indexed=indexed))
        return path

    def file(self, name):
        path = (self.root / name).resolve()
        require(path.is_relative_to(self.root), "Evidence path escapes run root")
        require(path.is_file(), f"{self.role}: missing {name}")
        return self._record(path, name, self.index is not None)

    def json(self, name):
        return json.loads(self.file(name).read_text(encoding="utf-8-sig"))


def manifest_for(path, filename, role, collected):
    if path is None or not (Path(path) / filename).is_file():
        return None, dict(status="NOT_RUN", path=str(Path(path).resolve()) if path else None)
    evidence = Evidence(path, role, collected)
    manifest = evidence.json(filename)
    return evidence, manifest


def source_rows(stage, role, evidence, manifest):
    result = []
    sources = manifest.get("source_sha256", manifest.get("sources", {}))
    for name, digest in sources.items():
        if not name.startswith(("hardware/", "software/arm_dma/")):
            continue
        path = evidence.file("source/" + name)
        require(sha(path) == digest, f"{role}: source hash differs {name}")
        result.append(dict(stage=stage, role=role, source=name, sha256=digest))
    return result


def collect_system(path, stage, collected, sources):
    ev, m = manifest_for(path, "run_manifest.json", stage + ":system", collected)
    result = dict(status=m.get("status", "UNKNOWN"), path=str(Path(path).resolve()) if path else None)
    if ev is None:
        return result
    result.update(manifest_sha256=sha(ev.root / "run_manifest.json"), variant=m.get("variant"),
                  validation=m.get("validation"), tool_results=m.get("tool_results", []),
                  fixed_evidence=m.get("fixed_evidence"), resources=None, timing=None)
    if m.get("status") != "complete":
        result["incomplete_reason"] = m.get("error", "System run has not completed")
        return result
    require(ev.index is not None, "Completed system has no artifact hash index")
    require(m.get("variant") == "fixed", "Summary fixed stage points to a different system variant")
    require(m.get("baseline", {}).get("fixed_contract_sha256") == CONTRACT_SHA, "System contract differs")
    require(all(m.get("validation", {}).get(k) == "PASS" for k in ("configuration", "actual_DMA_PS_simulation", "board_implementation")), "System gate incomplete")
    sources.extend(source_rows(stage, "system", ev, m))
    text = ev.file("reports/utilization_route.rpt").read_text(encoding="utf-8", errors="replace")
    resource = {}
    for key, label in (("lut", "Slice LUTs"), ("ff", "Slice Registers"), ("bram_tiles", "Block RAM Tile"), ("dsp", "DSPs")):
        match = re.search(r"^\|\s*" + re.escape(label) + r"\s*\|\s*([\d,.]+)\s*\|", text, re.M)
        require(match is not None, "Missing post-route resource " + key)
        value = float(match[1].replace(",", ""))
        resource[key] = value if key == "bram_tiles" else int(value)
    timing = ev.json("reports/timing.json")
    require(timing == m.get("timing") and min(timing.values()) >= 0, "System route timing differs or failed")
    ev.file("reports/timing_route.rpt")
    outputs = {}
    for name, extension in (("bitstream", "bit"), ("xsa", "xsa")):
        artifact = ev.file("design/mfcc_dma_fixed." + extension)
        require(sha(artifact) == m["outputs"][name]["sha256"], "System output identity differs")
        outputs[name] = dict(path=str(artifact), sha256=sha(artifact))
    result.update(resources=resource, timing=timing, outputs=outputs,
                  fixed_source_sha256={k: v for k, v in m["source_sha256"].items() if k.startswith("hardware/fixed/")})
    return result


def collect_arm(path, stage, system, collected, sources):
    ev, m = manifest_for(path, "build_manifest.json", stage + ":arm", collected)
    result = dict(status=m.get("status", "UNKNOWN"), path=str(Path(path).resolve()) if path else None)
    if ev is None or m.get("status") != "complete":
        return result
    require(ev.index is not None, "Completed ARM run has no artifact hash index")
    require(m.get("variant") == "fixed" and m.get("arm", {}).get("status") == "BUILT_NOT_BOARD_RUN", "ARM is not a completed fixed system build")
    if system["status"] == "complete":
        require(m.get("system_manifest_sha256") == system["manifest_sha256"], "ARM/system manifest binding differs")
    sources.extend(source_rows(stage, "arm", ev, m))
    elf = ev.file("binaries/mfcc_dma_demo.elf")
    require(sha(elf) == m["arm"]["elf_sha256"], "ARM ELF identity differs")
    result.update(elf_sha256=sha(elf), manifest_sha256=sha(ev.root / "build_manifest.json"),
                  system_run=m.get("system_run"), xsa_sha256=m["arm"]["xsa_sha256"])
    return result


def collect_board(path, stage, system, arm, collected, trial_rows):
    ev, m = manifest_for(path, "run_manifest.json", stage + ":board", collected)
    result = dict(status=m.get("status", "UNKNOWN"), path=str(Path(path).resolve()) if path else None,
                  timing_status="NOT_RUN", numerical_acceptance="NOT_ACCEPTED")
    if ev is None or m.get("status") != "BOARD_DEVELOPMENT_BIT_EXACT_PASS":
        return result
    require(ev.index is not None, "Completed board run has no artifact hash index")
    ident = ev.json("identity.json")
    require(ident.get("variant") == "fixed", "Board result is not fixed")
    if system["status"] == "complete":
        require(ident["system_manifest_sha256"] == system["manifest_sha256"], "Board/system binding differs")
    if arm["status"] == "complete":
        require(ident["arm_manifest_sha256"] == arm["manifest_sha256"], "Board/ARM binding differs")
    expected = ev.file("expected_records.bin").read_bytes()
    require(len(expected) == 166608 and hashlib.sha256(expected).hexdigest() == RECORD_SHA, "Expected fixed records differ from baseline")
    pcm = ev.file("development_pcm.bin")
    require(sha(pcm) == PCM_SHA, "Development PCM differs")
    actual = ev.file("development/results.bin").read_bytes()
    require(actual == expected, "Development raw values/metadata differ")
    require(m.get("development_bit_exact_passed") is True, "Development gate absent")
    result.update(development_bit_match=True, result_sha256=RECORD_SHA, identity_sha256=sha(ev.root / "identity.json"),
                  samples=85920, frames=534, outputs=6942, timing_boundary=m.get("timing_boundary"))
    if not m.get("timing_measured"):
        return result
    summary = ev.json("timing_summary.json")
    require(summary.get("same_elf_session") is True and summary.get("warmups") == 3 and summary.get("repeats") == 30 and summary.get("all33_outputs_verified") is True, "Timing policy or validation differs")
    raw = ev.file("timing/trials.bin").read_bytes()
    history = ev.file("timing/history.bin").read_bytes()
    require(len(raw) == 33 * 168 and len(history) == 33 * 166608, "Raw trials/history count differs")
    require(ev.file("timing/results.bin").read_bytes() == expected, "Final timing result differs")
    clocks = ev.json("timing/clocks.json")
    timer_hz = ident["timer_hz"]
    pl_hz = clocks["pl_configured_hz"]
    require(timer_hz == 333333343 and abs(pl_hz - 99999999) < 1, "Board clock condition differs from baseline")
    durations, busy, wfi = [], [], []
    for i in range(33):
        offset = i * 168
        ticks, busy_cycles, wfi_ticks = struct.unpack_from("<3Q", raw, offset)
        s = dict(zip(STAT_NAMES, struct.unpack_from("<32I", raw, offset + 24)))
        status, mismatches, discarded, sequence = struct.unpack_from("<i3I", raw, offset + 152)
        require((status, mismatches, discarded, sequence) == (0, 0, int(i < 3), i), "Raw trial status/order mismatch")
        required = dict(samples=85920, frames=534, input_received=85920, input_consumed=85920,
                        output_captured=6942, output_sent=6942, tx_bytes=171840, rx_bytes=166608,
                        input_bytes=171840, output_bytes=166608, rx_actual_bytes=166608,
                        core_status=2, core_error_flags=0, core_error_detail=0,
                        irq_tx_count=1, irq_rx_count=1, irq_core_count=1, irq_timer_count=0,
                        deadline_fired=0, recovery_attempted=0, recovery_failed=0)
        require(all(s[k] == v for k, v in required.items()), f"Trial {i} transport/IRQ/error counters differ")
        require(0 < wfi_ticks <= ticks and 0 < busy_cycles and s["wfi_count"] > 0, "Invalid timing counters")
        clip_ms, busy_ms, wfi_ms = ticks * 1000 / timer_hz, busy_cycles * 1000 / pl_hz, wfi_ticks * 1000 / timer_hz
        require(busy_ms <= clip_ms + 10e3 / timer_hz + 2e3 / pl_hz, "BUSY exceeds enclosing board interval")
        output = history[i * 166608:(i + 1) * 166608]
        require(output == expected, f"Trial {i} raw values/metadata differ")
        trial_rows.append(dict(stage=stage, trial=i, warmup=int(i < 3), elapsed_ticks=ticks,
                               pl_busy_cycles=busy_cycles, wfi_ticks=wfi_ticks, clip_ms=clip_ms,
                               pl_busy_ms=busy_ms, wfi_bracket_ms=wfi_ms, output_sha256=RECORD_SHA))
        if i >= 3:
            durations.append(clip_ms); busy.append(busy_ms); wfi.append(wfi_ms)
    calculated = dict(clip_ms=stats(durations), hardware_busy_ms=stats(busy), wfi_bracket_ms=stats(wfi))
    for name, values in calculated.items():
        for metric in ("min", "median", "mean", "max", "p95"):
            require(math.isclose(values[metric], summary[name][metric], rel_tol=1e-12, abs_tol=1e-12), "Raw timing disagrees with saved summary")
    result.update(timing_status="MEASURED_3_WARMUP_30_REPEATS", raw_history_bit_match=True,
                  timer_hz=timer_hz, pl_configured_hz=pl_hz, **calculated,
                  amortized_ms_per_frame=calculated["clip_ms"]["median"] / 534)
    return result


def collect_full(path, stage, collected, sources, case_rows, latency_rows, interval_rows, skip_frames):
    ev, m = manifest_for(path, "run_manifest.json", stage + ":full", collected)
    result = dict(status=m.get("status", "UNKNOWN"), path=str(Path(path).resolve()) if path else None,
                  instrumentation_status="NOT_RUN")
    if ev is None or m.get("status") != "complete":
        return result
    require(ev.index is not None, "Completed full RTL run has no artifact hash index")
    protocol = ev.json("protocol.json")
    require(protocol.get("status") == "PASS" and protocol.get("mismatches") == 0 and m.get("simulation") == protocol, "Full RTL protocol/manifest differs")
    require(m.get("c_contract", {}).get("sha256") == CONTRACT_SHA, "Full RTL numeric contract differs")
    require(m.get("accuracy_accepted") is False, "Fixed numeric acceptance changed")
    sources.extend(source_rows(stage, "full", ev, m))
    result.update(full_corpus=m.get("full_corpus"), frames=m.get("frames"), protocol=protocol,
                  vivado_exit_code=m.get("vivado_exit_code"), clock_ns=m.get("clock_ns"),
                  manifest_sha256=sha(ev.root / "run_manifest.json"),
                  numerical_acceptance="NOT_ACCEPTED", source_sha256=m.get("source_sha256"))
    if not all((ev.root / name).is_file() for name in ("stage_events.csv", "stage_metrics.csv")):
        return result
    clock_ns = float(m["clock_ns"])
    cases = m["cases"]
    with ev.file("stage_metrics.csv").open(newline="", encoding="utf-8-sig") as stream:
        metrics = [{k: int(v) for k, v in row.items()} for row in csv.DictReader(stream)]
    require(len(metrics) == len(cases), "Stage metrics have missing cases")
    for n, row in enumerate(metrics):
        require(row["case"] == n and row["frames"] == cases[n]["frames"], "Stage metrics case mismatch")
        case_rows.append(dict(stage=stage, case_id=cases[n]["id"], group=cases[n]["group"],
                              clock_ns=clock_ns, simulated_case_ms=row["cycles"] * clock_ns / 1e6,
                              mel_reads_per_frame=row["mel_reads"] / row["frames"] if row["frames"] else None, **row))
    frames = {}
    with ev.file("stage_events.csv").open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            c, frame, cycle = int(row["case"]), int(row["frame"]), int(row["cycle"])
            require(0 <= c < len(cases) and 0 <= frame < cases[c]["frames"] and row["event"] in EVENTS, "Unexpected frame/event identity")
            values = frames.setdefault((c, frame), {})
            require(row["event"] not in values, "Duplicate stage event")
            values[row["event"]] = cycle
    require(len(frames) == m["frames"], "Stage events have missing frames")
    for (c, frame), events in sorted(frames.items()):
        require(set(events) == set(EVENTS), "Incomplete per-frame stage events")
        row = dict(stage=stage, case=c, case_id=cases[c]["id"], group=cases[c]["group"], frame=frame, clock_ns=clock_ns)
        row.update({event + "_cycle": cycle for event, cycle in events.items()})
        for start, end in DELTAS:
            delta = events[end] - events[start]
            require(delta >= 0, "Stage event ordering is negative")
            row[start + "_to_" + end + "_cycles"] = delta
            row[start + "_to_" + end + "_us"] = delta * clock_ns / 1000
        latency_rows.append(row)
    for c, case in enumerate(cases):
        for event in ("frame_admit", "fft_first", "mel_first", "mfcc_first", "mfcc_last"):
            for scope, first in (("all_consecutive_frames", 0), ("tail_consecutive_frames", skip_frames)):
                intervals = [frames[(c, f)][event] - frames[(c, f - 1)][event] for f in range(max(1, first + 1), case["frames"])]
                if not intervals:
                    continue
                require(min(intervals) > 0, "Non-increasing frame events")
                values = stats(intervals)
                interval_rows.append(dict(stage=stage, case=c, case_id=case["id"], group=case["group"], event=event,
                                           scope=scope, leading_frame_ids_excluded=first, clock_ns=clock_ns,
                                           **{key + ("" if key == "count" else "_cycles"): value for key, value in values.items()},
                                           median_us=values["median"] * clock_ns / 1000))
    development = next((i for i, c in enumerate(cases) if c["group"] == "development"), None)
    if development is not None and cases[development]["frames"]:
        e = frames[(development, 0)]
        result["development_first_frame_admit_to_accepted_mfcc_cycles"] = e["mfcc_first"] - e["frame_admit"]
        result["development_first_frame_admit_to_accepted_mfcc_us"] = (e["mfcc_first"] - e["frame_admit"]) * clock_ns / 1000
        ii = next((r for r in interval_rows if r["stage"] == stage and r["case"] == development and r["event"] == "frame_admit" and r["scope"] == "tail_consecutive_frames"), None)
        result["development_observed_tail_frame_admission_interval"] = ii
    result["instrumentation_status"] = "COMPLETE_ACCEPTED_TRANSACTION_EVENTS"
    return result


def write_csv(path, rows, fallback):
    fields = list(dict.fromkeys(key for row in rows for key in row)) if rows else fallback
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory under build")
    parser.add_argument("--run-spec", type=Path, help="Optional JSON list of {stage, system, arm, board, full} paths; replaces default stages")
    parser.add_argument("--ii-skip-frames", type=int, default=4, help="Leading frame IDs omitted from observed tail-interval statistics")
    defaults = dict(system=BUILD / "system_dma/i10", arm=BUILD / "arm_dma/i01", board=BASELINE_BOARD,
                    full=BUILD / "fixed_full_rtl/full_616_repro_20261004_07")
    for stage in ("baseline", "sparse", "pipelined"):
        for role in ("system", "arm", "board", "full"):
            parser.add_argument(f"--{stage}-{role}", type=Path, default=defaults[role] if stage == "baseline" else None)
    args = parser.parse_args(argv)
    require(args.ii_skip_frames >= 0, "II skip count must be nonnegative")
    output = args.output.resolve()
    require(output.is_relative_to(BUILD.resolve()), "Summary output must remain under build")
    require(not output.exists(), "Refusing to overwrite summary output")
    specs = json.loads(args.run_spec.read_text(encoding="utf-8-sig")) if args.run_spec else [
        dict(stage=stage, **{role: getattr(args, stage + "_" + role) for role in ("system", "arm", "board", "full")})
        for stage in ("baseline", "sparse", "pipelined")]
    require(isinstance(specs, list) and specs and len({s["stage"] for s in specs}) == len(specs), "Run specification needs unique stage labels")
    require(all(re.fullmatch(r"[A-Za-z0-9_-]+", s["stage"]) for s in specs), "Invalid stage label")
    for spec in specs:
        for role in ("system", "arm", "board", "full"):
            if spec.get(role):
                source = Path(spec[role]).resolve()
                require(not output.is_relative_to(source) and not source.is_relative_to(output), "Summary output overlaps an input run")
    output.mkdir(parents=True)
    artifacts, sources, trials, cases, latencies, intervals, stages, errors = [], [], [], [], [], [], [], []
    for spec in specs:
        stage = spec["stage"]
        result = dict(stage=stage, numerical_acceptance="NOT_ACCEPTED")
        try:
            result["system"] = collect_system(spec.get("system"), stage, artifacts, sources)
            result["arm"] = collect_arm(spec.get("arm"), stage, result["system"], artifacts, sources)
            result["board"] = collect_board(spec.get("board"), stage, result["system"], result["arm"], artifacts, trials)
            result["full"] = collect_full(spec.get("full"), stage, artifacts, sources, cases, latencies, intervals, args.ii_skip_frames)
            if result["system"]["status"] == result["full"]["status"] == "complete":
                selected = lambda values: {k: v for k, v in values.items() if k.startswith("hardware/fixed/") and Path(k).suffix in (".sv", ".mem")}
                require(selected(result["system"]["fixed_source_sha256"]) == selected(result["full"]["source_sha256"]), "System and full RTL measurements use different candidate sources")
            result["collection_status"] = "COLLECTED"
        except (ValueError, OSError, KeyError, TypeError) as error:
            result["collection_status"] = "INCONSISTENT_EVIDENCE"
            result["error"] = str(error)
            errors.append(dict(stage=stage, error=str(error)))
            for rows in (trials, cases, latencies, intervals):
                rows[:] = [row for row in rows if row["stage"] != stage]
        stages.append(result)
    table = []
    baseline = next((s.get("board", {}) for s in stages if s["stage"] == "baseline" and s["collection_status"] == "COLLECTED"), {})
    baseline_ms = baseline.get("clip_ms", {}).get("median")
    for stage in stages:
        system, arm, board, full = (stage.get(role, {}) for role in ("system", "arm", "board", "full"))
        accepted = stage["collection_status"] == "COLLECTED"
        timing = board.get("clip_ms", {}) if accepted else {}
        resources = (system.get("resources") or {}) if accepted else {}
        route = (system.get("timing") or {}) if accepted else {}
        ii = (full.get("development_observed_tail_frame_admission_interval") or {}) if accepted else {}
        table.append(dict(stage=stage["stage"], collection_status=stage["collection_status"],
                          system_status=system.get("status", "NOT_RUN"), arm_status=arm.get("status", "NOT_RUN"),
                          board_status=board.get("status", "NOT_RUN"), timing_status=board.get("timing_status", "NOT_RUN") if accepted else "INCONSISTENT_EVIDENCE",
                          full_status=full.get("status", "NOT_RUN"), full_frames=full.get("frames"), full_corpus=full.get("full_corpus"),
                          numerical_acceptance="NOT_ACCEPTED", clip_median_ms=timing.get("median"),
                          clip_min_ms=timing.get("min"), clip_p95_ms=timing.get("p95"), clip_max_ms=timing.get("max"),
                          clip_median_over_534_ms=timing["median"] / 534 if timing else None,
                          pl_busy_median_ms=board.get("hardware_busy_ms", {}).get("median") if accepted else None,
                          baseline_clip_time_ratio=baseline_ms / timing["median"] if baseline_ms and timing else None,
                          simulated_first_frame_admit_to_accepted_result_us=full.get("development_first_frame_admit_to_accepted_mfcc_us") if accepted else None,
                          simulated_tail_frame_admission_interval_median_us=ii.get("median_us"),
                          simulated_tail_frame_admission_interval_count=ii.get("count"),
                          lut=resources.get("lut"), ff=resources.get("ff"), bram_tiles=resources.get("bram_tiles"), dsp=resources.get("dsp"),
                          post_route_setup_slack_ns=route.get("setup_slack_ns"), post_route_hold_slack_ns=route.get("hold_slack_ns")))
    summary = dict(schema_version=1, generated_at_utc=datetime.now(timezone.utc).isoformat(),
                   command=sys.argv if argv is None else [str(Path(__file__)), *argv], script_sha256=sha(__file__),
                   runs=[{k: str(v) if isinstance(v, Path) else v for k, v in s.items()} for s in specs],
                   notes=NOTES, ii_skip_frames=args.ii_skip_frames, stages=stages, comparison=table,
                   input_artifacts=artifacts, errors=errors, board_execution_performed=False)
    dump(output / "summary.json", summary)
    for name, rows, headers in (("comparison.csv", table, ["stage", "collection_status"]),
                                ("source_hashes.csv", sources, ["stage", "role", "source", "sha256"]),
                                ("board_trials.csv", trials, ["stage", "trial", "warmup", "clip_ms", "pl_busy_ms"]),
                                ("stage_cases.csv", cases, ["stage", "case", "frames", "cycles"]),
                                ("frame_latencies.csv", latencies, ["stage", "case", "frame"]),
                                ("stage_interval_statistics.csv", intervals, ["stage", "case", "event", "scope", "count"])):
        write_csv(output / name, rows, headers)
    lines = ["# Fixed hardware optimization evidence fragment", "", "Generated from explicit run paths; missing candidates remain NOT_RUN.", "",
             "| Stage | Board timing | Whole clip median (ms) | Whole clip / 534 (ms) | PL BUSY median (ms) | LUT / FF / BRAM / DSP | Setup / hold slack (ns) |",
             "|---|---|---:|---:|---:|---|---|"]
    fmt = lambda value: f"{value:.6f}" if value is not None else "NOT_RUN"
    for row in table:
        resource = " / ".join(str(row[k]) for k in ("lut", "ff", "bram_tiles", "dsp")) if row["lut"] is not None else "NOT_RUN"
        lines.append(f'| {row["stage"]} | {row["timing_status"]} | {fmt(row["clip_median_ms"])} | {fmt(row["clip_median_over_534_ms"])} | {fmt(row["pl_busy_median_ms"])} | {resource} | {fmt(row["post_route_setup_slack_ns"])} / {fmt(row["post_route_hold_slack_ns"])} |')
    lines += ["", "All fixed numerical acceptance statuses remain **NOT_ACCEPTED**. Exact raw/metadata equality is reported separately.", "",
              "The frame average in this table is whole-clip/534. Accepted first-result latency and consecutive frame-admission intervals are simulation measurements in separate CSVs, with cycle and microsecond units. Testbench backpressure is included. PL BUSY includes stalls and is not pure compute time.", "",
              "한국어 전달문: 동일 개발 음성과 DMA/인터럽트 조건에서 실제 3회 예열·30회 측정한 클립 시간을 비교한다. 모든 반복의 정수 출력과 메타데이터는 기준 비트열과 대조한다. 클립/534 환산 평균, 최초 수락 출력 지연, 프레임 수락 간격은 서로 다른 지표이며 고정소수점의 기존 수치 정확도 NOT_ACCEPTED 판정은 유지한다."]
    if errors:
        lines += ["", "Evidence inconsistencies (affected stages must not be used as accepted results):"] + [f'- {e["stage"]}: {e["error"]}' for e in errors]
    (output / "report_fragment.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copy2(__file__, output / Path(__file__).name)
    if args.run_spec:
        shutil.copy2(args.run_spec, output / "run_spec.json")
    dump(output / "artifact_hashes.json", {p.name: sha(p) for p in output.iterdir() if p.is_file() and p.name != "artifact_hashes.json"})
    print(json.dumps(dict(output=str(output), stages=len(stages), errors=errors), indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
