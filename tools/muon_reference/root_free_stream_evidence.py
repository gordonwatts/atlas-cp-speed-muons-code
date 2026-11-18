#!/usr/bin/env python3
"""Export the full positive seed domain and compare exact original-stream trace bits."""

import json
from pathlib import Path
from typing import Annotated

import awkward as ak
import numpy as np
import typer

from reference_histogram import open_local

app = typer.Typer(no_args_is_help=True)
Input = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
FIELDS = ("root_seed", "g0", "g1", "g2", "g3", "g4", "g_high_pt")
UUID = "79ee6652-311f-11f1-aa37-0445010abeef"


def check_metadata(file):
    metadata = json.loads(str(file["columnar_metadata"]))
    assert (metadata["source_uuid"], metadata["entry_start"], metadata["entry_stop"]) == (UUID, 0, 20_000)
    kinematic = tuple(state for state in metadata["states"] if state not in metadata["weight_only"])
    assert len(metadata["states"]) == 97 and len(kinematic) == 29
    return kinematic


def flat(listed):
    return ak.to_numpy(ak.flatten(listed))


@app.command("export-seeds")
def export_seeds(reference: Input, destination: Path):
    with open_local(reference) as file:
        check_metadata(file)
        tree = file["analysis"]
        nominal = tree.arrays(["mu_detail_momentum_called_NOSYS", "mu_momentum_root_seed_NOSYS"], how=dict)
    called = flat(nominal["mu_detail_momentum_called_NOSYS"]) == 1
    values = flat(nominal["mu_momentum_root_seed_NOSYS"])[called]
    assert len(values) == 14_997 and np.isfinite(values).all()
    assert np.array_equal(values, values.astype(np.uint32))
    assert ((values > 0) & (values <= np.iinfo(np.uint32).max)).all()
    with destination.open("x") as output:
        for seed in values.astype(np.uint32):
            print(seed, file=output)


@app.command()
def compare(reference: Input, candidate: Input, destination: Path):
    with open_local(reference) as a, open_local(candidate) as b:
        states = check_metadata(a)
        assert states == check_metadata(b)
        before, after = a["analysis"], b["analysis"]
        identities = ("entry", "runNumber", "eventNumber", "mcChannelNumber", "source_index")
        for name in identities:
            left, right = (tree[name].array(library="ak") for tree in (before, after))
            assert ak.almost_equal(left, right, rtol=0, atol=0), name
        results = {}
        for state in states:
            names = [f"mu_detail_momentum_called_{state}", *(f"mu_momentum_{field}_{state}" for field in FIELDS)]
            left = before.arrays(names, how=dict)
            right = after.arrays(names, how=dict)
            called = flat(left[names[0]]) == 1
            np.testing.assert_array_equal(called, flat(right[names[0]]) == 1)
            assert int(called.sum()) == 14_997
            mismatches = {}
            for field, name in zip(FIELDS, names[1:], strict=True):
                old = flat(left[name])[called].view(np.uint64)
                new = flat(right[name])[called].view(np.uint64)
                mismatches[field] = int(np.count_nonzero(old != new))
            results[state] = mismatches
    same = all(not any(row.values()) for row in results.values())
    with destination.open("x") as output:
        json.dump({"source_uuid": UUID, "entries": 20_000, "called_muons": 14_997,
                   "states": len(states), "bits_compared": 14_997 * len(states) * len(FIELDS),
                   "fields": FIELDS, "per_state_mismatches": results, "passed": same},
                  output, indent=2)
        output.write("\n")
    if not same:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
