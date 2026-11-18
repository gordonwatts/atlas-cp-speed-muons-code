"""Compare the first faithful C entry point to the delivered C entry point."""

import json
import os
from pathlib import Path

import typer

from benchmark_columnar_pairs import digest, summarize, time_process
from benchmark_final import isolated_environment
from campaign_host_activity import host_activity


def main(
    destination: Path, original: Path, expected: Path, initial_python: Path, initial_runner: Path,
    initial_provider: Path, payload: Path, final_python: Path, final_script: Path, final_provider: Path,
    initial_revision: str,
):
    if os.sched_getaffinity(0) != {18} or os.getpriority(os.PRIO_PROCESS, 0) != 10:
        raise RuntimeError("Launch with nice -n 10 taskset -c 18")
    artifacts = {item: {"path": str(path), "sha256": digest(path)} for item, path in {
        "original": original, "expected": expected, "initial_python": initial_python,
        "initial_provider": initial_provider, "payload": payload, "installed_python": final_python,
        "installed_visible_script": final_script, "installed_provider": final_provider,
        **{f"faithful_columnar/{name}": initial_runner.with_name(name) for name in (
            "run_muons.py", "raw_input.py", "muon_analysis.py", "outputs.py", "workflow.json"
        )},
    }.items()}
    release_env = {key: value for key, value in os.environ.items() if key not in {
        "PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_DEBUG", "LD_DEBUG_OUTPUT", "VIRTUAL_ENV",
    }}
    release_env.update(PYTHONNOUSERSITE="1")
    clean_env = isolated_environment(final_python)
    expected_digest = artifacts["expected"]["sha256"]
    destination.mkdir()
    (destination / "artifacts.json").write_text(json.dumps(artifacts, indent=2) + "\n")
    rows = []
    for pair in range(6):
        row = {"pair": pair, "discarded_warmup": pair == 0, "host_before": host_activity(), "runs": []}
        order = ("control", "candidate") if pair % 2 else ("candidate", "control")
        for variant in order:
            directory = destination / f"pair-{pair:02d}-{variant}"
            directory.mkdir()
            histogram = directory / "histogram.npz"
            if variant == "control":
                command = [initial_python, initial_runner, "histogram", original, histogram, "--mode", "full",
                           "--provider", initial_provider, "--momentum", payload, "--selection", payload,
                           "--efficiency", payload, "--chunk-size", "2000"]
                environment = release_env
            else:
                command = [final_python, final_script, original, histogram, "--mode", "full"]
                environment = clean_env
            timing = time_process(list(map(str, command)), directory, "job.log", environment)
            row["runs"].append({
                "variant": variant, "wall_seconds": timing["wall_seconds"], "cpu_seconds": timing["cpu_total_seconds"],
                "histogram": str(histogram), "time_receipt": str(directory / "job.log.json"),
            })
        row["host_after"] = host_activity()
        for run in row["runs"]:
            run["histogram_sha256"] = digest(Path(run["histogram"]))
            if run["histogram_sha256"] != expected_digest:
                raise RuntimeError(f"Histogram differs from accepted reference: {run['histogram']}")
        rows.append(row)
        (destination / "pairs.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(f"Pair {pair}: " + ", ".join(
            f"{run['variant']} {run['wall_seconds']:.4f} s" for run in row["runs"]), flush=True)
    wall = summarize(rows[1:], "wall_seconds")
    cpu = summarize(rows[1:], "cpu_seconds")
    report = {
        "scope": "original input, 97 states, full saved histogram, wall/CPU from process launch through exit",
        "control": "first accepted faithful C source, GCC 15 core and ROOT-linked provider",
        "candidate": "installed standalone analyst entry point, stock GCC 11 paired core and ROOT-free provider",
        "attribution": "combined workflow comparison across package, entry point, compiler and accepted implementation changes",
        "initial_revision": initial_revision, "artifacts": artifacts, "wall": wall, "cpu": cpu, "pairs": rows,
        "wall_speedup_initial_over_final": 1 / wall["median_ratio"],
        "wall_speedup_geometric_mean_interval95": [1 / value for value in reversed(
            wall["geometric_mean_student_interval_95"]
        )],
        "wall_timing_quality_pass": all(wall[variant]["timing_quality_pass"] for variant in ("control", "candidate")),
    }
    (destination / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    typer.run(main)
