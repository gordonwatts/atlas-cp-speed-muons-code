"""Check the persisted-link audit and the shape of the columnar products."""

import json
from pathlib import Path
from typing import Annotated

import awkward as ak
import numpy as np
import typer

from raw_input import flat, load_workflow, numpy, open_source

app = typer.Typer(no_args_is_help=True)
Input = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]


@app.command()
def links(footprint: Input, native_inventory: Input):
    """Compare the full uproot transport audit with the native xAOD link audit."""
    workflow = load_workflow()
    observed = json.loads(footprint.read_text(encoding="utf-8"))
    native = json.loads(native_inventory.read_text(encoding="utf-8-sig"))
    assert observed["uproot_source"] == "MemmapSource"
    assert observed["events_observed"] == native["entries"] == workflow["source"]["entries"]
    assert observed["muons_observed"] == native["muons"]
    assert observed["source"]["uuid"] == workflow["source"]["uuid"]
    for role, fields in observed["links"].items():
        expected = native["link_fields"][workflow["links"][role]["branch"]]
        assert fields.keys() == expected.keys(), role
        for key, counts in fields.items():
            for column in ("valid", "invalid", "null", "total"):
                assert counts[column] == expected[key][column], (role, key, column)
            for side in ("min", "max"):
                assert counts[f"index_{side}"] == expected[key][f"persisted_index_{side}"], (role, key, side)
    assert set(observed["links"]) == set(workflow["links"])
    print(f"Matched five link fields for {observed['muons_observed']} original muons")


@app.command()
def shapes(observations: Input, histogram: Input):
    """Verify the entry, event identity, jagged and full-state observer shapes."""
    workflow = load_workflow()
    expected_states = workflow["states"]["all_ordered"]
    with open_source(observations) as file:
        metadata = json.loads(str(file["columnar_metadata"]))
        tree = file["analysis"]
        assert metadata["source_uuid"] == workflow["source"]["uuid"]
        assert metadata["states"] == expected_states
        assert metadata["weight_only"] == workflow["states"]["weight_only"]
        assert tree.num_entries == metadata["entry_stop"] - metadata["entry_start"]
        event_fields = {"entry", "eventNumber", "runNumber", "mcChannelNumber", "randomRunNumber"}
        for chunk in tree.iterate(library="ak", how=dict, step_size=2000):
            assert numpy(chunk["eventNumber"]).dtype == np.dtype(np.uint64)
            counts = numpy(ak.num(chunk["source_pt"], axis=1))
            for name, values in chunk.items():
                if name not in event_fields:
                    np.testing.assert_array_equal(numpy(ak.num(values, axis=1)), counts, err_msg=name)
            np.testing.assert_array_equal(flat(chunk["source_index"]), flat(ak.local_index(chunk["source_pt"])))
        entry = numpy(tree["entry"].array(library="ak"))
        np.testing.assert_array_equal(entry, np.arange(metadata["entry_start"], metadata["entry_stop"], dtype=np.uint64))

    with np.load(histogram, allow_pickle=False) as payload:
        assert set(payload.files) == {"states", "pt_edges_GeV", "count", "sumw", "sumw2"}
        assert payload["states"].tolist() == expected_states
        np.testing.assert_allclose(payload["pt_edges_GeV"], np.linspace(0., 500., 101), rtol=0., atol=1e-12)
        assert all(payload[name].shape == (102, 97) for name in ("count", "sumw", "sumw2"))
        assert payload["count"].dtype == np.dtype(np.int64)
    print("Original entry and jagged fields, state order and histogram flows agree")


@app.command("histograms")
def histograms(first: Input, second: Input):
    """Compare two chunkings, or compare nominal and the full nominal category."""
    with np.load(first, allow_pickle=False) as a, np.load(second, allow_pickle=False) as b:
        np.testing.assert_array_equal(a["pt_edges_GeV"], b["pt_edges_GeV"])
        if a["states"].tolist() == b["states"].tolist():
            np.testing.assert_array_equal(a["count"], b["count"])
            for name in ("sumw", "sumw2"):
                np.testing.assert_allclose(a[name], b[name], rtol=1e-13, atol=1e-13)
        elif a["states"].tolist() == ["NOSYS"] and b["states"].tolist()[0] == "NOSYS":
            for name in ("count", "sumw", "sumw2"):
                np.testing.assert_array_equal(a[name][:, 0], b[name][:, 0])
        else:
            raise ValueError("Provide equal state coverage or nominal followed by full")
    print("Matched histogram content and flow")


if __name__ == "__main__":
    app()
