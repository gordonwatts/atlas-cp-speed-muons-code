#!/usr/bin/env python3
"""Observe Path B with effective-thread and loaded-library evidence."""

import os
from pathlib import Path
import sys

import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/muon_reference"))
from benchmark_reference import time_process


def main(destination: Path, bridge: Path, provider: Path, payload: Path, status_observer: Path):
    destination.mkdir()
    repo = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items() if key not in {"LD_PRELOAD", "LD_DEBUG", "LD_DEBUG_OUTPUT", "ATLAS_MUON_DISPATCH_AUDIT"}}
    env.update({
        "ATLAS_MUON_FRAMEWORK_BRIDGE": str(bridge), "ATLAS_MUON_PROVIDER": str(provider),
        "ATLAS_MUON_MOMENTUM": str(payload), "ATLAS_MUON_SELECTION": str(payload), "ATLAS_MUON_EFFICIENCY": str(payload),
        "PYTHONPATH": os.environ["RELEASE_PYTHONPATH"], "MUON_ETA_SAGITTA": "1", "MUON_MOMENTUM_SCHEME": "AllSys",
        "MUON_VALIDATION_OUTPUT": "1", "MUON_STATUS_OBSERVER_LIBRARY": str(status_observer),
    })
    env.pop("MUON_REFERENCE_OUTPUT", None)
    argv = [env["RELEASE_PYTHON"], str(repo / "atlas/muon/framework/bridge_entry.py"), "--direct-driver", "--dump-full-config", "-i", env["INPUT"], "-t", str(repo / "tools/muon_reference/smoke.yaml"), "-o", "reference"]
    time_process(argv, destination, "job.log", env)


if __name__ == "__main__":
    typer.run(main)
