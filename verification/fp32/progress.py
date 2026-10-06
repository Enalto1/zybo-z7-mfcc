"""Read bounded trace tails and report observed C12 completion counts only.

No files are written. These lower bounds are neither protocol/numerical results
nor timing measurements; simulator buffering may hide already completed work.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re

TAIL_BYTES = 512 * 1024


def tail_records(path: Path) -> tuple[list[bytes], dict]:
    if not path.is_file():
        return [], {"exists": False, "bytes_read": 0, "complete_records": 0}
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        start = max(0, size - TAIL_BYTES)
        stream.seek(start)
        raw = stream.read(size - start)
    first = raw.find(b"\n") + 1 if start else 0
    last = raw.rfind(b"\n") + 1
    records = raw[first:last].splitlines() if last >= first else []
    return records, {"exists": True, "file_size_at_open": size,
        "tail_start_byte": start, "bytes_read": len(raw), "complete_records": len(records),
        "ignored_trailing_bytes": len(raw) - last,
        "earlier_records_not_scanned": start > 0}


def observed(directory: Path, cases: dict[int, int]) -> tuple[dict, dict]:
    trace, trace_info = tail_records(directory / "traces.txt")
    summary, summary_info = tail_records(directory / "simulation_summary.txt")
    counts = {number: {"trace_observed_complete_frames": 0,
                       "summary_reported_complete_frames": 0} for number in cases}
    ignored = 0
    for record in trace:
        words = record.split()
        if len(words) != 7:
            ignored += 1
            continue
        try:
            number, stage, frame, index = map(int, words[:4])
        except ValueError:
            ignored += 1
            continue
        if number in cases and stage == 6 and index == 12 and 0 <= frame < cases[number]:
            counts[number]["trace_observed_complete_frames"] = max(
                counts[number]["trace_observed_complete_frames"], frame + 1)
    for record in summary:
        match = re.fullmatch(rb"PASS ([0-9]+) samples=([0-9]+) frames=([0-9]+) coeffs=([0-9]+) cycles=([0-9]+)", record)
        if match:
            number, _, frames, coeffs, _ = map(int, match.groups())
            if number in cases and frames == cases[number] and coeffs == frames * 13:
                counts[number]["summary_reported_complete_frames"] = frames
    for value in counts.values():
        value["observed_complete_frames_lower_bound"] = max(value.values())
    return counts, {"trace": trace_info, "summary": summary_info,
                    "unparsed_complete_trace_records": ignored}


def progress(run_dir: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    freeze = json.loads((run_dir / "freeze.json").read_text(encoding="utf-8-sig"))
    result = {"run_dir": str(run_dir), "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "read_only": True, "tail_byte_limit_per_file": TAIL_BYTES,
        "notice": "Observed completion lower bounds only; no protocol/numerical success or timing claim. Buffered writes may not yet be visible.",
        "segmented_replay": bool(freeze.get("segmented_replay")), "items": []}
    if freeze.get("segmented_replay"):
        for segment in freeze["segments"]:
            number = segment["number"]
            counts, read_info = observed(run_dir / "workers" / f"segment_{number:02d}", {number: segment["frames"]})
            item = counts[number]
            local_owned_first = segment["owned_frame_first"] - segment["origin_frame"]
            owned_expected = segment["owned_frame_stop"] - segment["owned_frame_first"]
            owned = min(owned_expected, max(0, item["observed_complete_frames_lower_bound"] - local_owned_first))
            result["items"].append({"worker": number, "expected_local_frames": segment["frames"],
                "expected_owned_frames": owned_expected, "observed_owned_frames_lower_bound": owned,
                **item, "reads": read_info})
        result["expected_owned_frames"] = freeze["case"]["frames"]
        result["observed_owned_frames_lower_bound"] = sum(item["observed_owned_frames_lower_bound"] for item in result["items"])
        result["progress"] = f"{result['observed_owned_frames_lower_bound']}/{result['expected_owned_frames']} owned frames observed"
    else:
        cases = {case["number"]: case["frames"] for case in freeze["cases"]}
        counts, read_info = observed(run_dir, cases)
        for case in freeze["cases"]:
            result["items"].append({"number": case["number"], "id": case["id"],
                "expected_frames": case["frames"], **counts[case["number"]]})
        result["reads"] = read_info
        result["expected_frames"] = sum(cases.values())
        result["observed_complete_frames_lower_bound"] = sum(item["observed_complete_frames_lower_bound"] for item in result["items"])
        result["progress"] = f"{result['observed_complete_frames_lower_bound']}/{result['expected_frames']} frames observed"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(progress(args.run_dir), indent=2))


if __name__ == "__main__":
    main()
