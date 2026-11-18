#!/usr/bin/env python3
"""Audit a 100-event release smoke against the corresponding unmodified input."""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

import ROOT
from PyUtils.MetaReader import read_metadata


def histogram_labels(handle, name):
    hist = handle.Get(name)
    return [hist.GetXaxis().GetBinLabel(i) for i in range(1, hist.GetNbinsX() + 1)]


def as_list(tree, branch):
    try:
        vector = getattr(tree, branch)
        values = (vector.at(index) for index in range(vector.size()))
        return [ord(value) if isinstance(value, str) else value for value in values]
    except Exception as exc:
        raise RuntimeError(f"Cannot read {branch} of {tree.GetBranch(branch).GetClassName()}") from exc


def audit(input_path, smoke_dir, comparison_dir=None):
    h = ROOT.TFile.Open(str(smoke_dir / "hist-reference.root"))
    source = ROOT.TFile.Open(input_path)
    source_tree = source.Get("CollectionTree")
    result = ROOT.TFile.Open(str(smoke_dir / "reference.root"))
    result_tree = result.Get("analysis")
    comparison = ROOT.TFile.Open(str(comparison_dir / "reference.root")) if comparison_dir else None
    comparison_tree = comparison.Get("analysis") if comparison else None
    comparison_branches = {branch.GetName() for branch in comparison_tree.GetListOfBranches()} if comparison_tree else set()
    states = histogram_labels(h, "systematicsList")
    only_weights = histogram_labels(h, "systematicsListOnlyWeights")
    branch_names = {branch.GetName() for branch in result_tree.GetListOfBranches()}
    root_metadata = read_metadata(input_path, mode="peeker")[input_path]
    source_tree.SetBranchStatus("*", False)
    for key in ("runNumber", "eventNumber", "mcChannelNumber", "RandomRunNumber"):
        source_tree.SetBranchStatus("EventInfoAuxDyn." + key, True)
    for key in ("pt", "eta", "phi", "muonType"):
        source_tree.SetBranchStatus("AnalysisMuonsAuxDyn." + key, True)
    assert result_tree.GetEntries() == 100
    assert states[0] == "NOSYS" and len(set(states)) == len(states)
    assert set(only_weights) < set(states)
    muon_branches = sorted(name for name in branch_names if name.startswith("mu_"))
    kinematics = [state for state in states if state not in only_weights and state != "NOSYS"]
    categories = {state: {"branches": sorted(name for name in muon_branches if name.endswith("_" + state)), "non_nominal_values": {}, "changed_selection_count": None} for state in states}
    prefixes = ("mu_pt", "mu_e", "mu_select_medium", "mu_effSF_medium", "mu_reco_effSF_medium", "mu_isol_effSF_medium", "mu_TTVA_effSF_medium")
    for state, category in categories.items():
        for prefix in prefixes:
            if state in kinematics or (state in only_weights and prefix == "mu_effSF_medium"):
                assert f"{prefix}_{state}" in branch_names
        if state in only_weights:
            assert not any(branch.startswith(("mu_pt_", "mu_e_", "mu_select_")) for branch in category["branches"])

    total_particles = 0
    loose_eta_fail = 0
    distinct_ids = set()
    muon_types = Counter()
    event_objects = Counter()
    run_numbers = Counter()
    nominal_selected = 0
    comparison_values = 0
    nominal_status_values = {}
    identity_sha256 = hashlib.sha256()
    efficiency_stages = ("reco", "isol", "TTVA")
    has_status_observer = "mu_detail_reco_sf_status_NOSYS" in branch_names
    selected_by_state = Counter()
    zero_events = []
    first = None
    eligible = {"all": 0, "weight-only": 0, "kinematic": 0}

    for entry in range(result_tree.GetEntries()):
        assert source_tree.GetEntry(entry) > 0 and result_tree.GetEntry(entry) > 0
        event = tuple(int(getattr(result_tree, key)) for key in ("runNumber", "eventNumber", "mcChannelNumber", "randomRunNumber"))
        assert event == tuple(int(getattr(source_tree, "EventInfoAuxDyn." + key)) for key in ("runNumber", "eventNumber", "mcChannelNumber", "RandomRunNumber"))
        assert event[:3] not in distinct_ids
        distinct_ids.add(event[:3])
        run_numbers[event[3]] += 1
        for output_branch, original_branch in (("source_pt", "pt"), ("source_eta", "eta"), ("source_phi", "phi"), ("source_muonType", "muonType")):
            assert as_list(result_tree, output_branch) == as_list(source_tree, "AnalysisMuonsAuxDyn." + original_branch)
        object_count = len(result_tree.source_pt)
        for index in range(object_count):
            identity_sha256.update(struct.pack("!QQQQQ", entry, index, *event[:3]))
        total_particles += object_count
        muon_types.update(as_list(result_tree, "source_muonType"))
        event_objects[object_count] += 1
        loose_eta_fail += sum(abs(value) >= 2.7 for value in result_tree.source_eta)
        if object_count == 0:
            zero_events.append(entry)
        assert as_list(result_tree, "mu_eta") == as_list(result_tree, "source_eta")
        assert as_list(result_tree, "mu_phi") == as_list(result_tree, "source_phi")

        arrays = {name: as_list(result_tree, name) for name in muon_branches}
        if comparison_tree:
            assert comparison_tree.GetEntry(entry) > 0
            for name in comparison_branches:
                if name.startswith(("mu_", "source_")):
                    assert as_list(result_tree, name) == as_list(comparison_tree, name), (entry, name)
                    comparison_values += object_count
                else:
                    assert getattr(result_tree, name) == getattr(comparison_tree, name), (entry, name)
                    comparison_values += 1
        assert all(len(array) == object_count for array in arrays.values())
        for name, array in arrays.items():
            if name.endswith("_NOSYS") and name.startswith("mu_detail_") and name.removesuffix("_NOSYS").endswith(("_gate", "_bad_muon", "_valid")):
                nominal_status_values.setdefault(name, Counter()).update(array)
        nominal_selected += sum(bool(status) for status in arrays["mu_select_medium_NOSYS"])
        for state, category in categories.items():
            selector = arrays.get(f"mu_select_medium_{state}", arrays["mu_select_medium_NOSYS"])
            selected_by_state[state] += sum(bool(status) for status in selector)
            if f"mu_detail_eta_gate_{state}" in arrays:
                gates = ("eta_gate", "pt_gate", "track_gate", "quality_gate", "isolation_gate")
                gate_arrays = [arrays[f"mu_detail_{name}_{state}"] for name in gates]
                assert selector == [int(eta == pt == track == 0xFFFFFFFF and quality == isolation == 1) for eta, pt, track, quality, isolation in zip(*gate_arrays, strict=True)]
                category.setdefault("explicit_momentum_status_when_called", Counter()).update(
                    validity for eta, validity in zip(gate_arrays[0], arrays[f"mu_detail_calibration_valid_{state}"], strict=True) if eta == 0xFFFFFFFF
                )
            weights = [arrays.get(f"{prefix}_{state}", arrays[f"{prefix}_NOSYS"]) for prefix in prefixes[-3:]]
            if has_status_observer:
                for stage, sf in zip(efficiency_stages, weights, strict=True):
                    status = f"mu_detail_{stage}_sf_status"
                    own = f"{status}_{state}"
                    codes = arrays.get(own, arrays[f"{status}_NOSYS"])
                    assert all(code in (1, 2) if selected else code == -1 and value == -1. for code, selected, value in zip(codes, selector, sf, strict=True)), (entry, stage, state)
                    category.setdefault("efficiency_status", {}).setdefault(stage, Counter()).update(codes)
            for expected, reco, isolation, ttva in zip(arrays[f"mu_effSF_medium_{state}"], *weights, strict=True):
                rounded = struct.unpack("!f", struct.pack("!f", reco * isolation))[0]
                assert expected == struct.unpack("!f", struct.pack("!f", rounded * ttva))[0], (entry, state)
            for branch in category["branches"]:
                if state == "NOSYS":
                    continue
                prefix = branch[: -(len(state) + 1)]
                pivot = f"{prefix}_NOSYS"
                if pivot not in arrays:
                    continue
                differences = sum(left != right for left, right in zip(arrays[pivot], arrays[branch], strict=True))
                category["non_nominal_values"].setdefault(prefix, 0)
                category["non_nominal_values"][prefix] += differences
        if first is None and object_count:
            first = {"entry": entry, "run_event_dsid_randomrun": event, "source_pt": as_list(result_tree, "source_pt"), "nominal_pt": arrays["mu_pt_NOSYS"], "nominal_selected": arrays["mu_select_medium_NOSYS"], "muon_types": as_list(result_tree, "source_muonType")}

    for state in states:
        categories[state]["selected_object_count"] = selected_by_state[state]
        categories[state]["changed_selection_count"] = categories[state]["non_nominal_values"].get("mu_select_medium", 0)
    nominal_branches = [name for name in muon_branches if "_NOSYS" in name]
    for state in states[1:]:
        eligible["all"] += bool(any(categories[state]["non_nominal_values"].values()))
        eligible["weight-only" if state in only_weights else "kinematic"] += bool(any(categories[state]["non_nominal_values"].values()))

    metadata_keys = ("file_type", "file_size", "nentries", "file_guid", "runNumbers", "lumiBlockNumbers", "mc_channel_number", "eventTypes", "processingTags", "GeoAtlas", "beam_energy", "conditions_tag", "project_name", "dataType", "AMITag", "generators", "mc_campaign", "SimFlavour", "_TagInfo")
    return {
        "source": {"path": input_path, "ROOT_uuid": str(source.GetUUID().AsString()), "CollectionTree_entries": source_tree.GetEntries(), "peeker_metadata": {key: root_metadata.get(key) for key in metadata_keys if key in root_metadata}, "peeker_metadata_keys": sorted(root_metadata)},
        "output": {"tree": "analysis", "entries": result_tree.GetEntries(), "branches": sorted(branch_names), "metadata_histogram": histogram_labels(h, "metadata"), "nominal_branches": nominal_branches},
        "identity": {"matched_distinct_events": len(distinct_ids), "matched_particles": total_particles, "sha256_network_uint64_entry_index_run_event_dsid": identity_sha256.hexdigest(), "source_multiplicities": dict(event_objects), "source_types": dict(muon_types), "source_abs_eta_gte_2p7": loose_eta_fail, "empty_entries": zero_events, "random_run_numbers": dict(run_numbers), "first_nonempty": first},
        "states": {"all_ordered": states, "kinematic": kinematics, "weight_only": only_weights, "categories": categories, "number_with_any_changed_product": eligible},
        "nominal_selected": nominal_selected,
        "comparison": {"path": str(comparison_dir) if comparison_dir else None, "compared_values": comparison_values},
        "nominal_status_values": nominal_status_values,
    }


if __name__ == "__main__":
    smoke_dir = Path(sys.argv[1])
    comparison_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    evidence = audit(os.environ["INPUT"], smoke_dir, comparison_dir)
    (smoke_dir / "smoke-evidence.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print("Output entries:", evidence["output"]["entries"])
    print("Identity:", evidence["identity"])
    print("Nominal selected:", evidence["nominal_selected"])
    print("Upstream branch comparison:", evidence["comparison"])
    print("Observed nominal decorations:", evidence["nominal_status_values"])
    print("Observed nominal efficiency status:", evidence["states"]["categories"]["NOSYS"].get("efficiency_status"))
    print("Kinematic states:", evidence["states"]["kinematic"])
    print("Weight-only basenames:", sorted({re.sub(r"__1(?:up|down)$", "", state) for state in evidence["states"]["weight_only"]}))
    print("Response counts:", evidence["states"]["number_with_any_changed_product"])
    print("Unchanged products:", [state for state, category in evidence["states"]["categories"].items() if state != "NOSYS" and category["non_nominal_values"] and not any(category["non_nominal_values"].values())])
