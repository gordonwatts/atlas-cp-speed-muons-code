#!/usr/bin/env python3
"""Run serial release and histogram legs with production-only output."""

import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import threading
import time

import numpy as np
import typer

from reference_histogram import systematic_labels


def host_snapshot():
    return {
        "load_average_1_5_15_minutes": os.getloadavg(),
        "cpu18_kernel_counters": next(line.split()[1:] for line in Path("/proc/stat").read_text().splitlines() if line.startswith("cpu18 ")),
        "processes": subprocess.run(("ps", "-eo", "pid,nlwp,ni,psr,%cpu,comm", "--sort=-%cpu"), check=True, capture_output=True, text=True).stdout.splitlines()[:16],
    }


def sample_process(pid, stop, samples, libraries):
    process = Path(f"/proc/{pid}")
    while not stop.is_set():
        try:
            status = dict(line.split(":", 1) for line in (process / "status").read_text().splitlines())
            samples.append({"threads": int(status["Threads"]), "affinity": status["Cpus_allowed_list"].strip()})
            for line in (process / "maps").read_text().splitlines():
                path = line.split()[-1]
                if path.startswith("/") and ".so" in path:
                    libraries.add(path)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return
        stop.wait(0.20)


def time_process(command, directory, log_name, env):
    samples = []
    libraries = set()
    stop = threading.Event()
    before = host_snapshot()
    with (directory / log_name).open("wb") as log:
        start_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        start = time.perf_counter()
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT)
        observer = threading.Thread(target=sample_process, args=(process.pid, stop, samples, libraries))
        observer.start()
        status = process.wait()
        seconds = time.perf_counter() - start
        end_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        stop.set()
        observer.join()
    user = end_usage.ru_utime - start_usage.ru_utime
    system = end_usage.ru_stime - start_usage.ru_stime
    summary = {
        "command": command,
        "exit_code": status,
        "wall_seconds": seconds,
        "cpu_user_seconds": user,
        "cpu_system_seconds": system,
        "cpu_total_seconds": user + system,
        "samples": samples,
        "observed_libraries": sorted(libraries),
        "host_before": before,
        "host_after": host_snapshot(),
    }
    (directory / f"{log_name}.json").write_text(json.dumps(summary, indent=2) + "\n")
    if status:
        raise subprocess.CalledProcessError(status, command)
    if not samples or any(sample != {"threads": 1, "affinity": "18"} for sample in samples):
        raise RuntimeError(f"Worker or CPU affinity differs from the one-thread contract in {directory / log_name}")
    return summary


def assess(values):
    mean = statistics.mean(values)
    if len(values) < 3:
        return {"eligible_count": len(values), "mean_seconds": mean, "timing_quality_pass": False}
    spread = statistics.stdev(values)
    median = statistics.median(values)
    maximum_relative_deviation = max(abs(value - median) for value in values) / median
    return {"eligible_count": len(values), "mean_seconds": mean, "sample_sd_seconds": spread, "sd_over_mean": spread / mean, "max_relative_deviation_from_median": maximum_relative_deviation, "timing_quality_pass": spread / mean <= 0.05 and maximum_relative_deviation <= 0.10}


def compare_histograms(metadata, oracle, rows, events):
    anchor = {}
    loaded_oracle = np.load(oracle) if oracle else None
    for row in rows:
        with np.load(Path(row["directory"]) / "histogram.npz") as observed:
            mode = row["mode"]
            baseline = anchor.setdefault(mode, {key: observed[key].copy() for key in observed.files})
            for key, reference in baseline.items():
                np.testing.assert_array_equal(observed[key], reference)
            states = observed["states"].tolist()
            assert states == (metadata if mode == "full" else ["NOSYS"])
            if loaded_oracle and mode == "full":
                for key in loaded_oracle.files:
                    np.testing.assert_array_equal(observed[key], loaded_oracle[key])
            row["histogram_verification"] = {"states": len(states), "event_limit": events, "selected_nominal": int(observed["count"][:, 0].sum()), "exact_across_repetitions": True, "exact_full_oracle": bool(loaded_oracle and mode == "full")}
    np.testing.assert_array_equal(anchor["nominal"]["pt_edges_GeV"], anchor["full"]["pt_edges_GeV"])
    for quantity in ("count", "sumw", "sumw2"):
        np.testing.assert_array_equal(anchor["nominal"][quantity][:, 0], anchor["full"][quantity][:, 0])
    if loaded_oracle:
        loaded_oracle.close()


def main(destination: Path, observer_oracle: Path, events: int = 20000, blocks: int = 4):
    destination.mkdir()
    base = Path(__file__).resolve().parent
    release = os.environ["RELEASE_PYTHON"]
    original = Path(os.environ["INPUT"])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.environ["RELEASE_PYTHONPATH"]
    env.update({"MUON_MOMENTUM_SCHEME": "AllSys", "MUON_ETA_SAGITTA": "1", "MUON_REFERENCE_OUTPUT": "production", "MUON_VALIDATION_OUTPUT": "0"})
    env.pop("MUON_STATUS_OBSERVER_LIBRARY", None)
    clean = {key: value for key, value in env.items() if key not in {"PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "LD_PRELOAD", "ROOT_INCLUDE_PATH"}}
    metadata, _ = systematic_labels(observer_oracle / "hist-reference.root")
    ledger = []
    for block in range(blocks):
        modes = ("stored", "nominal", "full") if block % 2 == 0 else ("full", "nominal", "stored")
        for mode in modes:
            run = destination / f"block-{block + 1:02d}-{mode}"
            run.mkdir()
            if mode == "stored":
                framework = [release, os.environ["CP_RUNNER"]]
                config = "stored.yaml"
            else:
                framework = [release, str(base / "run_reference.py")]
                config = "smoke.yaml"
            framework.extend(("--direct-driver", "-i", str(original), "-t", str(base / config), "-o", "reference", "-e", str(events)))
            if mode != "full":
                framework.append("--no-systematics")
            ref = time_process(framework, run, "framework.log", env)
            hist_command = [sys.executable, str(base / "reference_histogram.py"), "reference.root", "hist-reference.root", "histogram.npz"]
            if mode == "stored":
                hist_command.append("--stored")
            hist = time_process(hist_command, run, "histogram.log", clean)
            sizes = {name: (run / name).stat().st_size for name in ("reference.root", "hist-reference.root", "histogram.npz")}
            sizes["original_file"] = original.stat().st_size
            fields = ("wall_seconds", "cpu_total_seconds")
            ledger.append({"block": block + 1, "mode": mode, "discarded_warmup": block == 0, "directory": str(run), "framework": {name: ref[name] for name in fields}, "histogram": {name: hist[name] for name in fields}, "paired_wall_seconds": ref[fields[0]] + hist[fields[0]], "paired_cpu_seconds": ref[fields[1]] + hist[fields[1]], "file_bytes": sizes})
            print(f"Block {block + 1} {mode}: {ref[fields[0]]:.4f} s framework and {hist[fields[0]]:.4f} s histogram", flush=True)
            (destination / "observations.json").write_text(json.dumps(ledger, indent=2) + "\n")

    compare_histograms(metadata, observer_oracle / "histogram.npz" if events == 20000 else None, ledger, events)
    timing = {}
    for mode in ("stored", "nominal", "full"):
        rows = [row for row in ledger if row["mode"] == mode and not row["discarded_warmup"]]
        if not rows:
            continue
        timing[mode] = {"framework": assess([row["framework"]["wall_seconds"] for row in rows]), "histogram": assess([row["histogram"]["wall_seconds"] for row in rows]), "paired": assess([row["paired_wall_seconds"] for row in rows])}
    evidence = {"runs": ledger, "cohorts": timing, "protocol": {"cache_policy": "No cache eviction; warm shared OS and CVMFS caches after each cohort's first run", "CPU": 18, "one_analysis_thread": True, "timing_monitor_interval_seconds": 0.20, "comparison_outside_timers": True, "release_python": release, "histogram_python": sys.executable}}
    (destination / "benchmark.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    typer.run(main)
