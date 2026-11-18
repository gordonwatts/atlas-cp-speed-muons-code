#!/usr/bin/env python3
"""Hash all 40 original-order native momenta and auxiliary lanes in each state."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).with_name("columnar")))
import run_muons
from muon_analysis import MOMENTUM_OUTPUTS, NativeWorkflow, TRACE_OUTPUTS

app = typer.Typer(no_args_is_help=True)
FIELDS = MOMENTUM_OUTPUTS + TRACE_OUTPUTS


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path, result):
    with path.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")


@app.command()
def capture(provider: Path, payload: Path, original: Path, histogram: Path, evidence: Path):
    assert len(FIELDS) == 40
    state_counts = Counter()
    stat_calls = Counter()
    accumulators = {}

    class Observer(NativeWorkflow):
        def __init__(self, providers, momentum_payload, selection_payload, efficiency_payload, observe):
            super().__init__(providers, momentum_payload, selection_payload, efficiency_payload, True)

        def calibrate(self, state, common, selection_inputs):
            values, selected = super().calibrate(state, common, selection_inputs)
            lanes = accumulators.setdefault(state, {name: hashlib.sha256() for name in FIELDS})
            state_counts[state] += len(values["called"])
            stat_calls[state] += int(np.count_nonzero(values["sagitta_stat_comb_status"] != -1))
            for name in FIELDS:
                lanes[name].update(np.asarray(values[name], dtype="<f8").tobytes())
            return values, selected

    run_muons.NativeWorkflow = Observer
    run_muons.execute(original, histogram, run_muons.Mode.full, (provider,), payload, payload, payload)
    assert len(state_counts) == 29 and set(state_counts.values()) == {14_997}
    save(evidence, {
        "original_uuid": "79ee6652-311f-11f1-aa37-0445010abeef",
        "provider_sha256": digest(provider), "payload_sha256": digest(payload),
        "histogram_sha256": digest(histogram), "states": list(state_counts), "lanes": FIELDS,
        "objects_by_state": dict(state_counts), "stat_calls_by_state": dict(stat_calls),
        "words": sum(state_counts.values()) * len(FIELDS),
        "little_endian_bit_sha256": {
            state: {name: lane.hexdigest() for name, lane in fields.items()} for state, fields in accumulators.items()
        },
    })


@app.command()
def compare(first: Path, second: Path, output: Path):
    control, candidate = [json.loads(path.read_text()) for path in (first, second)]
    for key in ("original_uuid", "payload_sha256", "histogram_sha256", "states", "lanes", "objects_by_state", "stat_calls_by_state", "words"):
        assert control[key] == candidate[key]
    mismatches = []
    for state, fields in control["little_endian_bit_sha256"].items():
        for name, expected in fields.items():
            observed = candidate["little_endian_bit_sha256"][state][name]
            if expected != observed:
                mismatches.append({"state": state, "field": name, "control": expected, "candidate": observed})
    save(output, {
        "words": control["words"], "stat_calls": sum(control["stat_calls_by_state"].values()),
        "control_provider_sha256": control["provider_sha256"], "candidate_provider_sha256": candidate["provider_sha256"],
        "per_state_lane_hash_mismatches": mismatches,
    })
    if mismatches:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
