"""Record generated IP port/parameter contracts without modifying vendor files."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

NAMES = ("fp32_pcm16", "fp32_mul", "fp32_addsub", "fp32_log", "fp32_fft512")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def values(parameters):
    return {key: value[0]["value"] for key, value in parameters.items()}


def read_contract(run):
    ips = []
    for name in NAMES:
        xci = run / f"project/fp32_ips.srcs/sources_1/ip/{name}/{name}.xci"
        veo = run / f"project/fp32_ips.gen/sources_1/ip/{name}/{name}.veo"
        inst = json.loads(xci.read_text(encoding="utf-8-sig"))["ip_inst"]
        ports = []
        for direction, upper, lower, port in re.findall(
            r"//\s+(input|output)\s+wire\s+(?:\[(\d+)\s*:\s*(\d+)\]\s+)?(\w+)",
            veo.read_text(encoding="utf-8-sig"),
        ):
            ports.append(dict(name=port, direction=direction,
                              width=int(upper)-int(lower)+1 if upper else 1))
        ips.append(dict(name=name, vlnv=inst["component_reference"],
            revision=inst["ip_revision"], xci=str(xci), xci_sha256=sha(xci),
            veo_sha256=sha(veo), ports=ports,
            component_parameters=values(inst["parameters"]["component_parameters"]),
            model_parameters=values(inst["parameters"]["model_parameters"])))
    return dict(source="actual generated JSON XCI and VEO", simulation_passed=False, ips=ips)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = read_contract(args.run.resolve())
    if args.compare:
        old = read_contract(args.compare.resolve())
        result["compared_run"] = str(args.compare.resolve())
        differences = {}
        for before, after in zip(old["ips"], result["ips"]):
            difference = {}
            for group in ("component_parameters", "model_parameters"):
                difference[group] = {key: [before[group].get(key), after[group].get(key)]
                    for key in sorted(before[group].keys() | after[group].keys())
                    if before[group].get(key) != after[group].get(key)}
            difference["ports_added"] = [p for p in after["ports"] if p not in before["ports"]]
            difference["ports_removed"] = [p for p in before["ports"] if p not in after["ports"]]
            differences[after["name"]] = difference
        result["differences"] = differences
    output = args.run / "reports/ip_contract.json"
    if output.exists():
        parser.error(f"Existing report is preserved: {output}")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
