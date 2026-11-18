#!/usr/bin/env python3
"""Compare full production columnar providers in adjacent AB/BA pairs."""

import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys

import typer

from campaign_host_activity import host_activity

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/muon_reference"))
from benchmark_reference import assess, time_process


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def summarize(rows, field):
    control = [next(run[field] for run in row["runs"] if run["variant"] == "control") for row in rows]
    candidate = [next(run[field] for run in row["runs"] if run["variant"] == "candidate") for row in rows]
    ratios = [b / a for a, b in zip(control, candidate, strict=True)]
    log_ratios = [math.log(ratio) for ratio in ratios]
    log_center = statistics.mean(log_ratios)
    # Two-sided 95% critical value for the five declared measured pairs.
    width = 2.7764451051977987 * statistics.stdev(log_ratios) / math.sqrt(5)
    return {
        "control": {**assess(control), "median_seconds": statistics.median(control)},
        "candidate": {**assess(candidate), "median_seconds": statistics.median(candidate)},
        "paired_ratios": ratios,
        "median_ratio": statistics.median(ratios),
        "geometric_mean_ratio": math.exp(log_center),
        "geometric_mean_student_interval_95": [math.exp(log_center - width), math.exp(log_center + width)],
    }


def main(
    destination: Path, original: Path, control: Path, candidate: Path, payload: Path, expected: Path,
    candidate_runner: Path | None = None,
):
    for artifact in (original, control, candidate, payload, expected):
        if not artifact.is_file():
            raise typer.BadParameter(f"Missing artifact: {artifact}")
    destination.mkdir()
    runner = Path(__file__).with_name("columnar") / "run_muons.py"
    runners = {"control": runner, "candidate": candidate_runner or runner}
    runner_hashes = {variant: {name: digest(path.with_name(name)) for name in (
        path.name, "raw_input.py", "muon_analysis.py", "outputs.py", "workflow.json"
    )} for variant, path in runners.items()}
    libraries = {"control": control, "candidate": candidate}
    library_hashes = {variant: digest(path) for variant, path in libraries.items()}
    if library_hashes["control"] == library_hashes["candidate"] and runner_hashes["control"] == runner_hashes["candidate"]:
        raise ValueError("Paired production variants use the same binary and driver sources")
    expected_hash = digest(expected)
    environment = dict(os.environ)
    runtime = Path(sys.executable).resolve()
    rows = []
    for pair in range(6):
        row = {"pair": pair, "discarded_warmup": pair == 0, "host_before": host_activity(), "runs": []}
        order = ("control", "candidate") if pair % 2 else ("candidate", "control")
        for variant in order:
            directory = destination / f"pair-{pair:02d}-{variant}"
            directory.mkdir()
            histogram = directory / "histogram.npz"
            command = [
                sys.executable, str(runners[variant]), "histogram", str(original), str(histogram),
                "--mode", "full", "--provider", str(libraries[variant]),
                "--momentum", str(payload), "--selection", str(payload), "--efficiency", str(payload),
                "--chunk-size", "2000",
            ]
            timing = time_process(command, directory, "job.log", environment)
            row["runs"].append({
                "variant": variant,
                "wall_seconds": timing["wall_seconds"],
                "cpu_seconds": timing["cpu_total_seconds"],
                "time_receipt": str(directory / "job.log.json"),
                "histogram": str(histogram),
            })
        row["host_after"] = host_activity()
        for run in row["runs"]:
            path = Path(run["histogram"])
            run["histogram_bytes"] = path.stat().st_size
            run["histogram_sha256"] = digest(path)
            if run["histogram_sha256"] != expected_hash:
                raise ValueError(f"The production endpoint changed in {path}")
        rows.append(row)
        (destination / "pairs.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(f"Pair {pair}: " + ", ".join(f"{run['variant']} {run['wall_seconds']:.4f} s" for run in row["runs"]), flush=True)
    retained = rows[1:]
    wall, cpu = (summarize(retained, field) for field in ("wall_seconds", "cpu_seconds"))
    threshold = 0.995
    low, high = wall["geometric_mean_student_interval_95"]
    if not all(wall[stage]["timing_quality_pass"] for stage in libraries):
        outcome = "inconclusive-cohort-quality"
    elif high < threshold:
        outcome = "resolved-gain"
    elif low > threshold:
        outcome = "subthreshold"
    else:
        outcome = "inconclusive-threshold"
    report = {
        "outcome": outcome, "ratio_threshold": threshold, "clock": "process launch to exit",
        "scope": "original file, full, 97 states, 2000-entry chunks, CPU 18, one effective worker",
        "shared_payload_sha256": digest(payload), "accepted_histogram_sha256": expected_hash,
        "installed_python": sys.executable, "base_python": str(runtime),
        "provider_paths": {variant: str(path) for variant, path in libraries.items()},
        "provider_sha256": library_hashes, "runner_sha256": runner_hashes,
        "runner_paths": {variant: str(path) for variant, path in runners.items()},
        "wall": wall, "cpu": cpu, "pairs": rows,
    }
    (destination / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    typer.run(main)
