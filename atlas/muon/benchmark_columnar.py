#!/usr/bin/env python3
"""Measure the faithful original-input columnar workflow at fixed output scope."""

import json
import os
from pathlib import Path
import sys

import numpy as np
import typer
from campaign_host_activity import host_activity

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/muon_reference"))
from benchmark_reference import assess, time_process


def compare_output(candidate, reference, mode):
    with np.load(candidate) as result, np.load(reference) as expected:
        fields = ("count", "sumw", "sumw2")
        if mode == "nominal":
            assert result["states"].tolist() == ["NOSYS"]
            for key in fields:
                np.testing.assert_array_equal(result[key], expected[key][:, :1])
        else:
            np.testing.assert_array_equal(result["states"], expected["states"])
            for key in fields:
                np.testing.assert_array_equal(result[key], expected[key])
        np.testing.assert_array_equal(result["pt_edges_GeV"], expected["pt_edges_GeV"])


def main(destination: Path, implementation: Path, provider: Path, payload: Path, accepted_full: Path, stored_reference: Path, blocks: int = 6):
    if blocks < 6:
        raise ValueError("The full cohort requires five measured runs after its first run")
    destination.mkdir()
    original = Path(os.environ["INPUT"])
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH", "LD_PRELOAD"}}
    rows = []
    activity = []
    for block in range(blocks):
        activity.append({"block": block + 1, "before": host_activity()})
        modes = ("stored", "nominal", "full") if block % 2 == 0 else ("full", "nominal", "stored")
        for mode in modes:
            run = destination / f"block-{block + 1:02d}-{mode}"
            run.mkdir()
            histogram = run / "histogram.npz"
            command = [sys.executable, str(implementation), "histogram", str(original), str(histogram), "--mode", mode]
            if mode != "stored":
                command += ["--provider", str(provider), "--momentum", str(payload), "--selection", str(payload), "--efficiency", str(payload)]
            observation = time_process(command, run, "columnar.log", env)
            rows.append({"block": block + 1, "mode": mode, "directory": str(run), "discarded_warmup": block == 0, "wall_seconds": observation["wall_seconds"], "cpu_total_seconds": observation["cpu_total_seconds"], "input_file_bytes": original.stat().st_size, "output_file_bytes": histogram.stat().st_size})
            (destination / "observations.json").write_text(json.dumps(rows, indent=2) + "\n")
            print(f"Block {block + 1} {mode}: {observation['wall_seconds']:.4f} s", flush=True)
        activity[-1]["after"] = host_activity()
        (destination / "host-activity.json").write_text(json.dumps(activity, indent=2) + "\n")

    for row in rows:
        mode = row["mode"]
        reference = stored_reference if mode == "stored" else accepted_full
        compare_output(Path(row["directory"]) / "histogram.npz", reference, mode)
        row["histogram_array_exact"] = True
    timing = {mode: assess([row["wall_seconds"] for row in rows if row["mode"] == mode and not row["discarded_warmup"]]) for mode in ("stored", "nominal", "full")}
    evidence = {"protocol": {"CPU": 18, "workers": 1, "cache_policy": "warm shared filesystem cache; no eviction", "validation_outside_timers": True, "priority": os.getpriority(os.PRIO_PROCESS, 0)}, "runs": rows, "cohorts": timing}
    (destination / "benchmark.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    typer.run(main)
