#!/usr/bin/env python3
"""Verify placement and sample diagnostics for the intermediate production profile."""

import json
from pathlib import Path
import re
import sys

import typer

sys.path.insert(0, str(Path(__file__).with_name("profile")))
from profile_evidence import file_identity, placement, sampled_profile


def read_timing(path):
    times = re.fullmatch(r"wall=([\d.]+) user=([\d.]+) system=([\d.]+)\s*", path.read_text())
    assert times is not None
    wall, user, system = map(float, times.groups())
    return {"wall_seconds": wall, "user_cpu_seconds": user, "system_cpu_seconds": system}


def main(directory: Path, accepted: Path, entrypoint: Path):
    summary = directory / "summary"
    summary.mkdir(exist_ok=True)
    expected = file_identity(accepted)
    framework = entrypoint.name == "bridge_entry.py"
    variants = {}
    for label in ("control", "sampled"):
        path = directory / label
        assert path.joinpath("status.txt").read_text().splitlines() == ["command=0", "monitor=0"]
        identity = file_identity(path / "histogram.npz")
        assert identity["sha256"] == expected["sha256"]
        variants[label] = {**read_timing(path / "time.txt"),
                           "histogram": identity, "placement": placement(path, entrypoint.resolve(), 18, label == "sampled", allow_transient_fork=framework)}
        if framework:
            variants[label]["histogram_outside_profile"] = read_timing(path / "histogram-time.txt")
            variants[label]["framework_ntuple"] = file_identity(path / "reference.root")
    control, sampled = variants["control"], variants["sampled"]
    assert control["placement"]["binary"]["sha256"] == sampled["placement"]["binary"]["sha256"]
    profile = sampled_profile(directory / "sampled", sampled["placement"]["pid"], summary, 15)
    report = {
        "scope": "Intermediate single-pass, full production original-file " + ("framework" if framework else "columnar") + " path",
        "timer": "Bash wall, user CPU and system CPU from command launch to exit",
        "original_sample": {"entries": 20000, "objects": 14997, "states": 97},
        "entrypoint": file_identity(entrypoint), "expected_histogram": expected,
        "capability": file_identity(directory / "capability.txt"),
        "variants": variants,
        "sampled_to_control_wall_ratio": sampled["wall_seconds"] / control["wall_seconds"],
        "sampled_to_control_cpu_ratio": (
            (sampled["user_cpu_seconds"] + sampled["system_cpu_seconds"]) /
            (control["user_cpu_seconds"] + control["system_cpu_seconds"])
        ),
        "profile": profile, "coarse_phases": None if framework else file_identity(directory / "phases" / "phases.json"),
    }
    with (summary / "profile.json").open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    typer.run(main)
