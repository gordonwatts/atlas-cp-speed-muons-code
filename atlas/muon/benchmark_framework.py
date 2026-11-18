#!/usr/bin/env python3
"""Benchmark the accepted framework bridge with ordinary output only."""

import json
import os
from pathlib import Path
import sys

import typer
from campaign_host_activity import host_activity

REPO = Path(__file__).resolve().parents[2]
REFERENCE = REPO / "tools/muon_reference"
sys.path.insert(0, str(REFERENCE))
from benchmark_reference import assess, compare_histograms, time_process
from reference_histogram import systematic_labels


def main(destination: Path, bridge: Path, provider: Path, payload: Path, accepted: Path, blocks: int = 4):
    destination.mkdir()
    original = Path(os.environ["INPUT"])
    clean = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_DEBUG", "LD_DEBUG_OUTPUT", "ATLAS_MUON_DISPATCH_AUDIT", "MUON_STATUS_OBSERVER_LIBRARY"}}
    env = dict(clean)
    env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
    env.update({"PYTHONPATH": os.environ["RELEASE_PYTHONPATH"], "ATLAS_MUON_FRAMEWORK_BRIDGE": str(bridge), "ATLAS_MUON_PROVIDER": str(provider), "ATLAS_MUON_MOMENTUM": str(payload), "ATLAS_MUON_SELECTION": str(payload), "ATLAS_MUON_EFFICIENCY": str(payload), "MUON_MOMENTUM_SCHEME": "AllSys", "MUON_ETA_SAGITTA": "1", "MUON_REFERENCE_OUTPUT": "production", "MUON_VALIDATION_OUTPUT": "0"})
    release = env["RELEASE_PYTHON"]
    rows = []
    activity = []
    fields = ("wall_seconds", "cpu_total_seconds")
    for block in range(blocks):
        activity.append({"block": block + 1, "before": host_activity()})
        modes = ("stored", "nominal", "full") if block % 2 == 0 else ("full", "nominal", "stored")
        for mode in modes:
            run = destination / f"block-{block + 1:02d}-{mode}"
            run.mkdir()
            stored = mode == "stored"
            config = REFERENCE / ("stored.yaml" if stored else "smoke.yaml")
            case_env = dict(env, ATLAS_MUON_STORED_CONTROL="1" if stored else "0")
            command = [release, str(REPO / "atlas/muon/framework/bridge_entry.py"), "--direct-driver", "-i", str(original), "-t", str(config), "-o", "reference", "-e", "20000"]
            if mode != "full":
                command.append("--no-systematics")
            ref = time_process(command, run, "framework.log", case_env)
            hist_command = [sys.executable, str(REFERENCE / "reference_histogram.py"), "reference.root", "hist-reference.root", "histogram.npz"]
            if stored:
                hist_command.append("--stored")
            hist = time_process(hist_command, run, "histogram.log", clean)
            sizes = {name: (run / name).stat().st_size for name in ("reference.root", "hist-reference.root", "histogram.npz")}
            sizes["original_file"] = original.stat().st_size
            row = {"block": block + 1, "mode": mode, "directory": str(run), "discarded_warmup": block == 0, "framework": {name: ref[name] for name in fields}, "histogram": {name: hist[name] for name in fields}, "paired_wall_seconds": ref["wall_seconds"] + hist["wall_seconds"], "paired_cpu_seconds": ref["cpu_total_seconds"] + hist["cpu_total_seconds"], "file_bytes": sizes}
            rows.append(row)
            print(f"Block {block + 1} {mode}: {ref['wall_seconds']:.4f} s framework and {hist['wall_seconds']:.4f} s histogram", flush=True)
            (destination / "observations.json").write_text(json.dumps(rows, indent=2) + "\n")
        activity[-1]["after"] = host_activity()
        (destination / "host-activity.json").write_text(json.dumps(activity, indent=2) + "\n")

    states, _ = systematic_labels(accepted / "hist-reference.root")
    compare_histograms(states, accepted / "histogram.npz", rows, 20000)
    timings = {}
    for mode in ("stored", "nominal", "full"):
        retained = [row for row in rows if row["mode"] == mode and not row["discarded_warmup"]]
        timings[mode] = {"framework": assess([row["framework"]["wall_seconds"] for row in retained]), "histogram": assess([row["histogram"]["wall_seconds"] for row in retained]), "paired": assess([row["paired_wall_seconds"] for row in retained])}
    evidence = {"protocol": {"CPU": 18, "one_analysis_thread": True, "priority": os.getpriority(os.PRIO_PROCESS, 0), "warm_shared_cache": True, "validation_outside_timers": True, "stored_mode": "ordinary stored control with the bridge loaded and no corrections scheduled"}, "runs": rows, "cohorts": timings}
    (destination / "benchmark.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    typer.run(main)
