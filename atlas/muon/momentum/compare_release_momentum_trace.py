#!/usr/bin/env python3
"""Compare original-release tool primitives with the 40-output diagnostic node."""

import csv
import json
from pathlib import Path
import sys

import awkward as ak
import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools/muon_reference"))
from reference_histogram import open_local


def main(release_trace: Path, candidate: Path, output: Path):
    with release_trace.open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 29 * 67
    fields = tuple(key for key in rows[0] if key not in {"entry", "index", "state"})
    state_names = tuple(dict.fromkeys(row["state"] for row in rows))
    branches = [f"mu_momentum_{field}_{state}" for state in state_names for field in fields]
    with open_local(candidate) as root_file:
        tree = root_file["analysis"]
        arrays = tree.arrays(branches, entry_stop=100, how=dict, library="ak")
    exact_fields = {"root_seed", "g0", "g1", "g2", "g3", "g4", "g_high_pt", "sagitta_called"}
    stats = {field: {"checked": 0, "exact": 0, "max_abs": 0.0, "failed": 0} for field in fields}
    first = []
    for row in rows:
        entry, index, state = int(row["entry"]), int(row["index"]), row["state"]
        for field in fields:
            original = float(row[field])
            observed = float(arrays[f"mu_momentum_{field}_{state}"][entry, index])
            equal = original == observed or (np.isnan(original) and np.isnan(observed))
            delta = abs(observed - original) if np.isfinite(original) and np.isfinite(observed) else 0.0
            passed = equal or (field not in exact_fields and np.isfinite(original) and np.isfinite(observed) and delta <= 2e-12 + 2e-12 * abs(original))
            result = stats[field]
            result["checked"] += 1
            result["exact"] += int(equal)
            result["failed"] += int(not passed)
            result["max_abs"] = max(delta, result["max_abs"])
            if not passed and len(first) < 20:
                first.append({"field": field, "entry": entry, "index": index, "state": state, "A": str(original), "C": str(observed)})
    passed = not any(item["failed"] for item in stats.values())
    starts = [int(row["root_seed"]) for row in rows if row["state"] == "NOSYS"]
    with output.open("x") as handle:
        json.dump({"passed": passed, "muon_states": len(rows), "states": state_names, "fields": stats, "first_failures": first, "nominal_seed_min": min(starts), "nominal_seed_max": max(starts), "nominal_zero_seeds": starts.count(0)}, handle, indent=2)
        handle.write("\n")
    print(f"Compared {len(fields)} release momentum primitive values per object state across {len(rows)} object states. Passed: {passed}")
    if not passed:
        print(json.dumps(first, indent=2))
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
