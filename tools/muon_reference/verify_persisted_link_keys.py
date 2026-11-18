#!/usr/bin/env python3
"""Match original ROOT.persKey and persIndex counts to native link resolution."""

from collections import Counter
import json
from pathlib import Path

import awkward as ak
import numpy as np
import typer

from reference_histogram import open_local


def main(original: Path, native_inventory: Path, output: Path):
    document = json.loads(native_inventory.read_text())
    with open_local(original) as root_file:
        tree = root_file["CollectionTree"]
        assert tree.num_entries == document["entries"]
        for name, inventory in document["link_fields"].items():
            branch = f"AnalysisMuonsAuxDyn.{name}"
            persisted = tree[branch].array(library="ak")
            key_field = f"{branch}.m_persKey"
            index_field = f"{branch}.m_persIndex"
            keys = ak.to_numpy(ak.flatten(persisted[key_field]))
            indices = ak.to_numpy(ak.flatten(persisted[index_field]))
            assert len(keys) == len(indices) == document["muons"]
            assert Counter(str(value) for value in keys) == Counter({key: value["total"] for key, value in inventory.items()})
            for key, value in inventory.items():
                value["persisted_index_min"] = int(indices[keys == int(key)].min())
                value["persisted_index_max"] = int(indices[keys == int(key)].max())
                value["persisted_key_count_verified"] = True
            assert int(np.count_nonzero((keys == 0) & (indices == 0xFFFFFFFF))) == sum(value["null"] for value in inventory.values())
    with output.open("x") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    typer.run(main)
