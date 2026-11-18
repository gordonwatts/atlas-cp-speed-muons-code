#!/usr/bin/env python3
"""Fill the frozen muon pT × systematic histograms with the release ntuple."""

from pathlib import Path

import awkward as ak
import hist
import numpy as np
import typer
import uproot


def open_local(path):
    return uproot.open(
        path,
        handler=uproot.source.file.MemmapSource,
        num_workers=1,
        use_threads=False,
        decompression_executor=uproot.TrivialExecutor(),
        interpretation_executor=uproot.TrivialExecutor(),
    )


def systematic_labels(metadata_path):
    with open_local(metadata_path) as root_file:
        states = root_file["systematicsList"].axis().labels()
        weight_only = root_file["systematicsListOnlyWeights"].axis().labels() if "systematicsListOnlyWeights" in root_file else []
    assert states and states[0] == "NOSYS" and len(states) == len(set(states))
    return states, weight_only or []


def fill_reference(ntuple, metadata, stored, cross_check=False):
    states, weight_only = systematic_labels(metadata)
    if stored:
        assert states == ["NOSYS"]
    axes = (
        hist.axis.Regular(100, 0, 500, underflow=True, overflow=True, name="pt_GeV"),
        hist.axis.StrCategory(states, growth=False, flow=False, name="systematic"),
    )
    weighted = hist.Hist(*axes, storage=hist.storage.Weight())
    unweighted = hist.Hist(*axes, storage=hist.storage.Int64())
    edges = weighted.axes[0].edges
    independent = np.zeros((3, 102, len(states))) if cross_check else None

    with open_local(ntuple) as root_file:
        tree = root_file["analysis"]
        branches = set()
        for state in states:
            kinematic = "NOSYS" if state in weight_only else state
            branches.add(f"mu_pt_{kinematic}")
            if not stored:
                branches.update({f"mu_select_medium_{kinematic}", f"mu_effSF_medium_{state}"})

        for chunk in tree.iterate(sorted(branches), library="ak", how=dict, step_size=2000):
            for index, state in enumerate(states):
                kinematic = "NOSYS" if state in weight_only else state
                pt = chunk[f"mu_pt_{kinematic}"]
                if stored:
                    weights = None
                else:
                    selected = chunk[f"mu_select_medium_{kinematic}"] != 0
                    pt = pt[selected]
                    weights = ak.to_numpy(ak.flatten(chunk[f"mu_effSF_medium_{state}"][selected])).astype(np.float64)
                    if not np.isfinite(weights).all():
                        raise ValueError(f"Non-finite selected weight for {state}")
                values = ak.to_numpy(ak.flatten(pt)).astype(np.float64) / 1000.
                if not np.isfinite(values).all():
                    raise ValueError(f"Non-finite selected momentum for {state}")
                weighted.fill(values, state, weight=weights, threads=1)
                unweighted.fill(values, state, threads=1)

                if cross_check:
                    bins = np.searchsorted(edges, values, side="right")
                    w = weights if weights is not None else np.ones_like(values)
                    independent[0, :, index] += np.bincount(bins, minlength=102)
                    independent[1, :, index] += np.bincount(bins, weights=w, minlength=102)
                    independent[2, :, index] += np.bincount(bins, weights=w * w, minlength=102)

    values = weighted.view(flow=True)
    counts = unweighted.view(flow=True)
    if cross_check:
        np.testing.assert_array_equal(counts, independent[0])
        np.testing.assert_allclose(values.value, independent[1], rtol=1e-13, atol=1e-13)
        np.testing.assert_allclose(values.variance, independent[2], rtol=1e-13, atol=1e-13)
    return {"states": np.asarray(states), "pt_edges_GeV": edges, "count": counts, "sumw": values.value, "sumw2": values.variance}


def main(ntuple: Path, metadata: Path, output: Path, stored: bool = False, cross_check: bool = False):
    if output.exists():
        raise FileExistsError(output)
    histograms = fill_reference(ntuple, metadata, stored, cross_check)
    with output.open("xb") as handle:
        np.savez_compressed(handle, **histograms)


if __name__ == "__main__":
    typer.run(main)
