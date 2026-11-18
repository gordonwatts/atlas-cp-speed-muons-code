#!/usr/bin/env python3
"""Expose C's original-file names and types without reading replacement data."""

import json
from pathlib import Path

import awkward as ak
import typer

from reference_histogram import open_local


def main(original: Path, release_inventory: Path, output: Path):
    native = json.loads(release_inventory.read_text())
    examples = native["native_track_link_examples"]
    with open_local(original) as root_file:
        tree = root_file["CollectionTree"]
        prefixes = (
            "AnalysisMuons", "InDetTrackParticles", "InDetForwardTrackParticles", "CombinedMuonTrackParticles",
            "ExtrapolatedMuonTrackParticles", "MuonSpectrometerTrackParticles", "PrimaryVertices", "EventInfo",
        )
        types = {name: dtype for name, dtype in tree.typenames().items() if name.startswith(prefixes)}
        stop = 1 + max(example["entry"] for example in examples)
        keys = ["EventInfoAuxDyn.RandomRunNumber", "AnalysisMuonsAuxDyn.muonType"]
        arrays = tree.arrays(keys, entry_stop=stop, how=dict, library="ak")
        links = {
            name: tree[branch].array(entry_stop=stop, library="ak")
            for name in examples[0]["links"]
            if (branch := f"AnalysisMuonsAuxDyn.{name}") in types
        }
        for example in examples:
            position = example["entry"]
            index = example["muon_index"]
            example["random_run_number"] = int(arrays[keys[0]][position])
            assert example["muon_type"] == int(arrays[keys[1]][position, index])
            for name, link in example["links"].items():
                if name in links:
                    link["uproot_persisted_fields_unmodified"] = ak.to_list(links[name][position, index])
        native.update({"original_path": str(original), "entries": tree.num_entries, "uproot_typenames": types})
    with output.open("x") as stream:
        json.dump(native, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    typer.run(main)
