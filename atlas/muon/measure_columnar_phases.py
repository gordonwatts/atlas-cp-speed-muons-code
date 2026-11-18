#!/usr/bin/env python3
"""Measure diagnostic phase and batch counts on the accepted C implementation."""

from collections import Counter
from pathlib import Path
import json
import sys
import time

import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).resolve().parent / "columnar"))
from muon_analysis import NativeWorkflow, StatePlan
from outputs import MuonHistogram, branch_footprint
from raw_input import MuonChunk, branch_plan, check_branches, load_workflow, open_source, verify_file
from run_muons import entry_ranges, read_chunk


class MeasureNode:
    def __init__(self, stage, node, times, calls, lanes):
        self.stage = stage
        self.node = node
        self.times = times
        self.calls = calls
        self.lanes = lanes

    def __call__(self, **arguments):
        start = time.perf_counter()
        result = self.node(**arguments)
        self.times[self.stage] += time.perf_counter() - start
        self.calls[self.stage] += 1
        self.lanes[self.stage] += result.shape[0]
        return result


class MeasuredWorkflow(NativeWorkflow):
    def __init__(self, provider, payload, times, calls, lanes):
        super().__init__([provider], payload, payload, payload, False)
        self.times = times
        for key in ("momentum", "eta_gate", "vertex_kind", "track_roles", "selection", "product"):
            setattr(self, key, MeasureNode(key, getattr(self, key), times, calls, lanes))
        self.efficiency = {key: MeasureNode(f"efficiency_{key}", node, times, calls, lanes) for key, node in self.efficiency.items()}

    def prepare(self, chunk):
        start = time.perf_counter()
        result = super().prepare(chunk)
        self.times["prepare_including_its_nodes"] += time.perf_counter() - start
        return result


def main(original: Path, provider: Path, payload: Path, histogram: Path, evidence: Path, accepted_histogram: Path, chunk_size: int = 2000):
    timings, counts, lanes = Counter(), Counter(), Counter()
    start = time.perf_counter()
    workflow = load_workflow()
    states = StatePlan.build(workflow, True)
    names = branch_plan(workflow)
    target = MuonHistogram(states.states)
    native = MeasuredWorkflow(provider, payload, timings, counts, lanes)
    timings["initialization"] = time.perf_counter() - start
    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        check_branches(tree, names, workflow["schema"])
        footprint = branch_footprint(tree, names)
        for first, last in entry_ranges(tree, 0, None, chunk_size):
            start = time.perf_counter()
            columns = read_chunk(tree, names, first, last)
            timings["uproot_read"] += time.perf_counter() - start
            start = time.perf_counter()
            chunk = MuonChunk(columns, first, workflow["links"])
            timings["chunk_construction"] += time.perf_counter() - start
            for state, result in native.apply(chunk, states):
                start = time.perf_counter()
                selected = result.selection["final"] == 1
                target.fill(state, result.momentum["pt"][selected], result.combined_weight[selected])
                timings["histogram_fill"] += time.perf_counter() - start
        input_source = source.file.source
        bytes_requested = input_source.num_requested_bytes
    start = time.perf_counter()
    target.save(histogram)
    timings["histogram_write"] = time.perf_counter() - start
    with np.load(histogram) as observed, np.load(accepted_histogram) as reference:
        for key in reference.files:
            np.testing.assert_array_equal(observed[key], reference[key])
    result = {"seconds": timings, "native_batch_calls": counts, "native_lanes": lanes, "branch_projection": footprint, "uproot_bytes_requested": bytes_requested, "accepted_histogram_exact": True, "mode": "diagnostic timer callbacks on production-output workflow"}
    with evidence.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    typer.run(main)
