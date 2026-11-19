"""Measure all three accepted workflows serially on the same original input."""

import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys

import typer

LOCAL = Path(__file__).resolve().parent
REFERENCE = LOCAL.parents[1] / "tools/muon_reference"
sys.path.insert(0, str(REFERENCE))
from benchmark_reference import assess, time_process
from benchmark_columnar import compare_output
from campaign_host_activity import host_activity


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def summary(values):
    return {**assess(values), "median_seconds": statistics.median(values)}


def ratio_summary(numerators, denominators):
    ratios = [a / b for a, b in zip(numerators, denominators, strict=True)]
    logs = list(map(math.log, ratios))
    center = statistics.mean(logs)
    # Five prespecified matched blocks after warmup, Student t(4), two-sided 95%.
    half = 2.776445105197799 * statistics.stdev(logs) / math.sqrt(len(logs))
    return {"ratios": ratios, "median": statistics.median(ratios),
            "geometric_mean": math.exp(center), "interval95": [math.exp(center - half), math.exp(center + half)]}


def isolated_environment(python):
    env = {key: os.environ[key] for key in ("HOME", "LANG", "LC_ALL", "TMPDIR", "TZ") if key in os.environ}
    env.update(PATH=f"{python.parent}:/usr/bin:/bin", PYTHONNOUSERSITE="1", OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1",
               VECLIB_MAXIMUM_THREADS="1", BLIS_NUM_THREADS="1", ROOT_MAX_THREADS="1")
    return env


def release_environment():
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_PRELOAD", "LD_DEBUG", "LD_DEBUG_OUTPUT", "ATLAS_MUON_DISPATCH_AUDIT",
                          "MUON_STATUS_OBSERVER_LIBRARY"} and not key.startswith("ATLAS_MUON_")}
    env.update(PYTHONPATH=os.environ["RELEASE_PYTHONPATH"], MUON_MOMENTUM_SCHEME="AllSys",
               MUON_ETA_SAGITTA="1", MUON_REFERENCE_OUTPUT="production", MUON_VALIDATION_OUTPUT="0")
    return env


def measure(argv, directory, name, env):
    record = time_process(list(map(str, argv)), directory, name + ".log", env)
    return {field: record[field] for field in ("wall_seconds", "cpu_total_seconds")}


def main(
    destination: Path, original: Path, bridge: Path, provider: Path, payload: Path,
    installed_python: Path, demo: Path, accepted_full: Path, accepted_stored: Path,
    modes: str = "stored,nominal,full", ab_only: bool = False,
):
    selected = modes.split(",")
    if len(set(selected)) != len(selected) or any(m not in {"stored", "nominal", "full"} for m in selected):
        raise typer.BadParameter("Use distinct comma-separated modes from stored,nominal,full")
    if os.sched_getaffinity(0) != {18} or os.getpriority(os.PRIO_PROCESS, 0) != 10:
        raise RuntimeError("Launch this driver with nice -n 10 taskset -c 18")
    artifacts = dict(input=original, bridge=bridge, provider=provider, payload=payload,
                     installed_python=installed_python, demo=demo, accepted_full=accepted_full,
                     accepted_stored=accepted_stored)
    for path in artifacts.values():
        if not path.is_absolute() or not path.is_file():
            raise typer.BadParameter(f"Expected absolute existing file: {path}")
    destination.mkdir()
    write_json(destination / "artifacts.json", {
        key: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for key, path in artifacts.items()
    })
    release = os.environ["RELEASE_PYTHON"]
    env_a = release_environment()
    env_b = dict(env_a, ATLAS_MUON_FRAMEWORK_BRIDGE=str(bridge), ATLAS_MUON_PROVIDER=str(provider),
                 ATLAS_MUON_MOMENTUM=str(payload), ATLAS_MUON_SELECTION=str(payload), ATLAS_MUON_EFFICIENCY=str(payload))
    env_c = isolated_environment(installed_python)
    histogram_env = isolated_environment(Path(sys.executable))
    # Use balanced adjacent orders when the C identity is unchanged.
    paths = "AB" if ab_only else "ABC"
    orders = ("BA", "AB", "BA", "AB", "BA", "AB") if ab_only else ("ABC", "CBA", "BAC", "CAB", "BCA", "ACB")
    ledger = []
    for block, order in enumerate(orders):
        mode_order = selected if block % 2 == 0 else list(reversed(selected))
        for mode in mode_order:
            entry = {"block": block, "warmup": block == 0, "mode": mode, "order": order,
                     "host_before": host_activity(), "paths": {}}
            for name in order:
                directory = destination / f"block-{block:02d}-{mode}-{name}"
                directory.mkdir()
                output = directory / "histogram.npz"
                if name == "C":
                    legs = {"columnar": measure([installed_python, demo, original, output, "--mode", mode],
                                                directory, "columnar", env_c)}
                else:
                    stored = mode == "stored"
                    if name == "A":
                        runner = os.environ["CP_RUNNER"] if stored else REFERENCE / "run_reference.py"
                        env = env_a
                    else:
                        runner = LOCAL / "framework/bridge_entry.py"
                        env = dict(env_b, ATLAS_MUON_STORED_CONTROL="1" if stored else "0")
                    config = REFERENCE / ("stored.yaml" if stored else "smoke.yaml")
                    command = [release, runner, "--direct-driver", "-i", original,
                               "-t", config, "-o", "reference", "-e", "20000"]
                    if mode != "full":
                        command.append("--no-systematics")
                    framework = measure(command, directory, "framework", env)
                    command = [sys.executable, REFERENCE / "reference_histogram.py",
                               "reference.root", "hist-reference.root", "histogram.npz"]
                    if stored:
                        command.append("--stored")
                    legs = {"framework": framework, "histogram": measure(command, directory, "histogram", histogram_env)}
                entry["paths"][name] = {
                    "legs": legs, "total": {field: sum(leg[field] for leg in legs.values())
                                             for field in ("wall_seconds", "cpu_total_seconds")},
                    "directory": str(directory), "files": {p.name: p.stat().st_size for p in directory.iterdir()
                                                            if p.suffix in {".root", ".npz"}},
                }
            entry["host_after"] = host_activity()
            # All launches and clocks have finished before validation begins.
            for result in entry["paths"].values():
                output = Path(result["directory"]) / "histogram.npz"
                compare_output(output, accepted_stored if mode == "stored" else accepted_full, mode)
                result["histogram_sha256"] = sha256(output)
            ledger.append(entry)
            write_json(destination / "runs.json", ledger)
            print(f"Block {block} {mode} {order}: all histograms checked", flush=True)

    summaries = {}
    for mode in selected:
        runs = [row["paths"] for row in ledger if row["mode"] == mode and not row["warmup"]]
        cohorts = {}
        for name in paths:
            parts = ("framework", "histogram", "total") if name != "C" else ("total",)
            for part in parts:
                legs = [row[name]["total"] if part == "total" else row[name]["legs"][part] for row in runs]
                cohorts[f"{name}_{part}"] = {field: summary([leg[field] for leg in legs])
                                              for field in ("wall_seconds", "cpu_total_seconds")}
        ratios = {}
        for field in ("wall_seconds", "cpu_total_seconds"):
            ratios[field] = {
                "B_framework_over_A_framework": ratio_summary([row["B"]["legs"]["framework"][field] for row in runs],
                                                               [row["A"]["legs"]["framework"][field] for row in runs]),
            }
            if not ab_only:
                ratios[field]["A_total_over_C_total"] = ratio_summary([row["A"]["total"][field] for row in runs],
                                                                      [row["C"]["total"][field] for row in runs])
        summaries[mode] = {"cohorts": cohorts, "matched": ratios}
    write_json(destination / "benchmark.json", {"summaries": summaries, "protocol": {
        "CPU": 18, "priority": 10, "warmup": 1, "measured_blocks": 5, "orders": orders,
        "paths": paths,
        "standalone": None if ab_only else "Fresh installed CPython, visible script, original PHYSLITE to closed histogram",
        "framework": "Release setup outside timer, ordinary production ntuple, separate histogram leg",
        "sampling": "One effective analysis worker; separate process monitor; generated outputs checked outside clocks",
        "cache": "Shared warm OS/CVMFS caches; no eviction",
    }})


if __name__ == "__main__":
    typer.run(main)
