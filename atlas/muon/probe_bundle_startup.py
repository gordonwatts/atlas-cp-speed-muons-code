#!/usr/bin/env python3
"""Measure loading the accepted correction set outside production clocks."""

import json
from pathlib import Path
import resource
import time

from correctionlib import _core, plugins
import typer


def rss_kib():
    return int(next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:")).split()[1])


def main(provider: Path, payload: Path, output: Path):
    start = time.perf_counter()
    plugins.load_library(provider.resolve())
    library_time = time.perf_counter() - start
    initial = rss_kib()
    handles = []
    parsed = []
    for count in range(1, 4):
        start = time.perf_counter()
        handles.append(_core.CorrectionSet.from_file(str(payload)))
        elapsed = time.perf_counter() - start
        assert handles[-1]["muon_selection_eta"].evaluate(0.0) == 0xFFFFFFFF
        parsed.append({"retained_sets": count, "load_seconds": elapsed, "rss_kib": rss_kib(), "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
    document = {"payload_bytes": payload.stat().st_size, "provider_open_seconds": library_time, "rss_before_loads_kib": initial, "loads_in_single_process": parsed, "interpretation": "isolated startup diagnostic; all CorrectionSets remain live"}
    with output.open("x") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    print(json.dumps(document, indent=2))


if __name__ == "__main__":
    typer.run(main)
