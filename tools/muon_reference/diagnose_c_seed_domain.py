#!/usr/bin/env python3
"""Audit the original event seed expression and all kinematic seed aliases."""

import json
from pathlib import Path

import awkward as ak
import numpy as np
import typer

from reference_histogram import open_local


def flatten(values):
    return ak.to_numpy(ak.flatten(values))


def main(observations: Path, output: Path):
    with open_local(observations) as file:
        metadata = json.loads(str(file["columnar_metadata"]))
        states = [state for state in metadata["states"] if state not in metadata["weight_only"]]
        trace = file["analysis"]
        names = ["eventNumber", "source_eta", "source_phi"]
        for state in states:
            names.extend([f"mu_momentum_root_seed_{state}", f"mu_detail_momentum_called_{state}"])
        data = trace.arrays(names, library="ak", how=dict)
    calls = flatten(data["mu_detail_momentum_called_NOSYS"]) == 1
    payload_seed = flatten(data["mu_momentum_root_seed_NOSYS"])[calls]
    eta = flatten(data["source_eta"]).astype(np.float64)[calls]
    phi = flatten(data["source_phi"]).astype(np.float64)[calls]
    event = flatten(ak.broadcast_arrays(data["eventNumber"], data["source_eta"])[0]).astype(np.float64)[calls]
    raw = 1 + np.abs(phi) * 1e6 + np.abs(eta) * 1e3 + event
    truncated = np.trunc(raw)
    safe = np.isfinite(truncated) & (truncated >= 0) & (truncated <= 0xFFFFFFFF)
    discrepancies = {}
    for state in states:
        mask = flatten(data[f"mu_detail_momentum_called_{state}"]) == 1
        np.testing.assert_array_equal(mask, calls)
        other = flatten(data[f"mu_momentum_root_seed_{state}"])[mask]
        discrepancies[state] = int(np.count_nonzero(other != payload_seed))
    result = {"called_muons": len(payload_seed), "states_checked": states, "cast_outside_domain": int((~safe).sum()), "seed_minimum": int(payload_seed.min()), "seed_maximum": int(payload_seed.max()), "zero_seed_count": int((payload_seed == 0).sum()), "formula_mismatches_in_domain": int(np.count_nonzero(payload_seed[safe] != truncated[safe])), "state_seed_vector_mismatches": discrepancies}
    with output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps(result, indent=2))
    if result["cast_outside_domain"] or result["formula_mismatches_in_domain"] or sum(discrepancies.values()):
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
