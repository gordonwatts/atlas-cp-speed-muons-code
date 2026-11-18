#!/usr/bin/env python3
"""Fill the muon pT histogram directly from the frozen PHYSLITE file."""

import os

# These limits must be set before importing NumPy or initializing hist.
for variable in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
):
    os.environ[variable] = "1"

from contextlib import nullcontext
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from muon_analysis import NativeWorkflow, StatePlan
from outputs import ChunkObservations, MuonHistogram, ObservationFile, add_link_counts, branch_footprint
from raw_input import MUON_PREFIX, MuonChunk, branch_plan, check_branches, flat, load_workflow, open_source, verify_file

app = typer.Typer(no_args_is_help=True)
Input = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Output = Annotated[Path, typer.Argument(dir_okay=False)]
FileOption = Annotated[Path, typer.Option(exists=True, dir_okay=False)]
Providers = Annotated[list[Path], typer.Option("--provider", exists=True, dir_okay=False)]
OptionalFile = Annotated[Path | None, typer.Option(exists=True, dir_okay=False)]
OptionalProviders = Annotated[list[Path] | None, typer.Option("--provider", exists=True, dir_okay=False)]
Start = Annotated[int, typer.Option(min=0)]
Stop = Annotated[int | None, typer.Option(min=0)]
ChunkSize = Annotated[int, typer.Option(min=1)]


class Mode(StrEnum):
    stored = "stored"
    nominal = "nominal"
    full = "full"


def entry_ranges(tree, start, stop, chunk_size):
    if stop is None:
        stop = tree.num_entries
    if not 0 <= start <= stop <= tree.num_entries or chunk_size < 1:
        raise ValueError("Requested entry interval or chunk size is outside the original file")
    if start == stop:
        yield start, stop
    for entry in range(start, stop, chunk_size):
        yield entry, min(stop, entry + chunk_size)


def read_chunk(tree, branches, start, stop):
    # Read exactly the declared branches and preserve their original jagged lists.
    split = [name for name in branches if tree[name].branches]
    leaves = [name for name in branches if name not in split]
    columns = tree.arrays(leaves, entry_start=start, entry_stop=stop, library="ak", how=dict)
    for name in split:
        columns[name] = tree[name].array(entry_start=start, entry_stop=stop, library="ak")
    if set(columns) != set(branches):
        raise RuntimeError("Uproot returned a different branch projection")
    if any(len(column) != stop - start for column in columns.values()):
        raise RuntimeError("A source branch changed the requested event count")
    return columns


def execute(
    original, histogram, mode, providers=(), momentum=None, selection=None, efficiency=None,
    *, start=0, stop=None, chunk_size=2000, observations=None,
):
    """Run a declared original-entry range and write histogram plus optional validation rows."""
    if histogram.resolve() == original.resolve() or histogram.exists():
        raise FileExistsError(histogram)
    if observations is not None and (observations.exists() or observations.resolve() == histogram.resolve()):
        raise FileExistsError(observations)

    workflow = load_workflow()
    stored = mode == Mode.stored
    plan = StatePlan.build(workflow, mode == Mode.full)
    branch_names = branch_plan(workflow, stored=stored, observe=observations is not None)
    histograms = MuonHistogram(plan.states)
    if stored:
        if observations is not None or providers or any((momentum, selection, efficiency)):
            raise ValueError("Stored mode takes the raw pT field and a histogram destination")
        nodes = None
    else:
        if not providers or not all((momentum, selection, efficiency)):
            raise ValueError("Pass a provider and momentum, selection and efficiency payloads")
        nodes = NativeWorkflow(providers, momentum, selection, efficiency, observations is not None)

    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        check_branches(tree, branch_names, workflow["schema"])
        metadata = {
            "source_uuid": str(source.file.uuid), "states": list(plan.states),
            "weight_only": [name for name in plan.states if name in plan.owners],
            "entry_start": start, "entry_stop": tree.num_entries if stop is None else stop,
        }
        with (ObservationFile(observations, metadata) if observations else nullcontext()) as observer:
            for first, last in entry_ranges(tree, start, stop, chunk_size):
                columns = read_chunk(tree, branch_names, first, last)
                if stored:
                    histograms.fill("NOSYS", flat(columns[MUON_PREFIX + "pt"], np.float64))
                    continue

                chunk = MuonChunk(columns, first, workflow["links"])
                current = ChunkObservations(chunk, plan) if observer else None
                for state, result in nodes.apply(chunk, plan):
                    passed = result.selection["final"] == 1
                    histograms.fill(state, result.momentum["pt"][passed], result.combined_weight[passed])
                    if current:
                        current.add(state, result)
                if current:
                    observer.extend(current.columns)

    histograms.save(histogram)


@app.command("histogram")
def histogram(
    original: Input,
    output: Output,
    mode: Annotated[Mode, typer.Option()] = Mode.full,
    provider: OptionalProviders = None,
    momentum: OptionalFile = None,
    selection: OptionalFile = None,
    efficiency: OptionalFile = None,
    start: Start = 0,
    stop: Stop = None,
    chunk_size: ChunkSize = 2000,
):
    """Run the production histogram path in stored, nominal or full mode."""
    execute(original, output, mode, provider or (), momentum, selection, efficiency,
            start=start, stop=stop, chunk_size=chunk_size)


@app.command()
def observe(
    original: Input,
    output: Output,
    observations: Annotated[Path, typer.Option(dir_okay=False)],
    provider: Providers,
    momentum: FileOption,
    selection: FileOption,
    efficiency: FileOption,
    start: Start = 0,
    stop: Stop = None,
    chunk_size: ChunkSize = 2000,
):
    """Write all original-object fields and the full histogram for acceptance."""
    execute(original, output, Mode.full, provider, momentum, selection, efficiency,
            start=start, stop=stop, chunk_size=chunk_size, observations=observations)


@app.command("inspect-input")
def inspect_input(
    original: Input,
    output: Output,
    stored: Annotated[bool, typer.Option()] = False,
    observe_fields: Annotated[bool, typer.Option()] = False,
    start: Start = 0,
    stop: Stop = None,
    chunk_size: ChunkSize = 2000,
):
    """Measure the declared branch read and audit all persisted track links."""
    if output.exists():
        raise FileExistsError(output)
    workflow = load_workflow()
    with original.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != workflow["source"]["sha256"] or original.stat().st_size != workflow["source"]["size"]:
        raise ValueError("Original input digest or size differs from the frozen provenance")
    plan = branch_plan(workflow, stored=stored, observe=observe_fields)
    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        check_branches(tree, plan, workflow["schema"])
        branches = branch_footprint(tree, plan)
        n_events = n_muons = 0
        links = {}
        for first, last in entry_ranges(tree, start, stop, chunk_size):
            columns = read_chunk(tree, plan, first, last)
            chunk = MuonChunk(columns, first, workflow["links"])
            n_events += last - first
            n_muons += chunk.muon_size
            if not stored:
                add_link_counts(chunk, links)
        source_io = source.file.source
        result = {
            "source": dict(workflow["source"], path=str(original.resolve())),
            "entry_start": start, "entry_stop": tree.num_entries if stop is None else stop,
            "events_observed": n_events, "muons_observed": n_muons,
            "branch_root_storage": branches, "links": links,
            "uproot_source": type(source_io).__name__,
            "uproot_chunks_requested": source_io.num_requested_chunks,
            "uproot_requests": source_io.num_requests,
            "uproot_bytes_requested": source_io.num_requested_bytes,
        }
    with output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
        target.write("\n")


if __name__ == "__main__":
    app()
