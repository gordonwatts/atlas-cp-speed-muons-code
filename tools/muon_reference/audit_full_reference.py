#!/usr/bin/env python3
"""Audit the full before-thinning release dataset against original PHYSLITE."""

from collections import Counter
import hashlib
import json
from pathlib import Path

import awkward as ak
import numpy as np
import typer

from reference_histogram import open_local, systematic_labels


EVENT_FIELDS = {
    "eventNumber": "EventInfoAuxDyn.eventNumber",
    "mcChannelNumber": "EventInfoAuxDyn.mcChannelNumber",
    "randomRunNumber": "EventInfoAuxDyn.RandomRunNumber",
    "runNumber": "EventInfoAuxDyn.runNumber",
}
SOURCE_FIELDS = {f"source_{field}": f"AnalysisMuonsAuxDyn.{field}" for field in ("eta", "muonType", "phi", "pt")}
GATES = ("eta_gate", "pt_gate", "track_gate", "quality_gate", "isolation_gate")
TOOLS = {"reco": "reco", "isol": "isolation", "TTVA": "ttva"}
TRACK_ACCEPT = 0xFFFFFFFF


def as_numpy(array):
    return ak.to_numpy(ak.flatten(array))


def validate_branch_contract(branches, states, weight_only, tool_states):
    kinematics = set(states) - set(weight_only)
    covariates = ("calibration_valid", "id_pt", "me_pt", "id_charge", "me_charge", "charge", "bad_muon", *GATES)
    coverage = {f"mu_detail_{field}": kinematics for field in covariates}
    coverage.update({"mu_pt": kinematics, "mu_e": kinematics, "mu_select_medium": kinematics, "mu_effSF_medium": set(states)})
    for stage, tool in TOOLS.items():
        coverage[f"mu_{stage}_effSF_medium"] = kinematics | tool_states[tool]
        coverage[f"mu_detail_{stage}_sf_status"] = kinematics | tool_states[tool]
    for quantity, expected in coverage.items():
        actual = {name[len(quantity) + 1:] for name in branches if name.startswith(quantity + "_")}
        assert expected == actual, (quantity, expected - actual, actual - expected)
    return coverage


def assert_exact(actual, expected, context):
    if not np.array_equal(actual, expected, equal_nan=True):
        indices = np.flatnonzero(~(np.equal(actual, expected) | (np.isnan(actual) & np.isnan(expected))))
        first = indices[0] if indices.size else None
        raise AssertionError((context, first, actual[first], expected[first]))


def update_counts(counter, values):
    unique, frequencies = np.unique(values, return_counts=True)
    counter.update({int(key): int(count) for key, count in zip(unique, frequencies, strict=True)})


def main(original: Path, run: Path, initialized_tools: Path, output: Path, production: Path | None = None, stored: Path | None = None):
    states, weight_only = systematic_labels(run / "hist-reference.root")
    tools = json.loads(initialized_tools.read_text())
    tool_states = {name: set(properties["recommended"]) for name, properties in tools.items()}
    assert set(weight_only) == set.union(*(tool_states[tool] for tool in TOOLS.values()))
    assert set(states) - set(weight_only) == {"NOSYS"} | tool_states["momentum"]
    per_state = {state: {"selected": 0, "sf_codes": {stage: Counter() for stage in TOOLS}, "momentum_codes": Counter(), "output_changes": Counter(), "nonfinite_values": Counter()} for state in states}
    raw_runs = Counter()
    random_runs = Counter()
    source_multiplicity = Counter()
    source_muon_types = Counter()
    n_muons = 0
    identities = hashlib.sha256()
    event_ids = hashlib.sha256()
    branch_checks = 0
    actual_replay_values = Counter()
    production_checks = 0
    stored_checks = 0
    stored_bin_counts = np.zeros(102, dtype=np.int64)
    nominal_reco_oov_examples = []

    with open_local(original) as raw_file, open_local(run / "reference.root") as reference_file:
        source = raw_file["CollectionTree"]
        reference = reference_file["analysis"]
        branches = reference.keys()
        coverage = validate_branch_contract(branches, states, weight_only, tool_states)
        uuid = str(raw_file.file.uuid)
        events = reference.num_entries
        assert events == source.num_entries == 20000
        production_file = open_local(production / "reference.root") if production else None
        stored_file = open_local(stored / "reference.root") if stored else None
        if stored_file:
            stored_tree = stored_file["analysis"]
            assert stored_tree.num_entries == events and stored_tree.keys() == ["mu_pt_NOSYS"]
        if production_file:
            production_tree = production_file["analysis"]
            assert production_tree.num_entries == events
            assert systematic_labels(production / "hist-reference.root") == (states, weight_only)
            expected_production = {f"{field}_{state}" for field in ("mu_pt", "mu_select_medium", "mu_effSF_medium") for state in coverage[field]}
            assert set(production_tree.keys()) == expected_production

        for start in range(0, events, 1000):
            end = min(start + 1000, events)
            raw = source.arrays(list(EVENT_FIELDS.values()) + list(SOURCE_FIELDS.values()), entry_start=start, entry_stop=end, how=dict, library="ak")
            observed = reference.arrays(entry_start=start, entry_stop=end, how=dict, library="ak")
            if production_file:
                ordinary = production_tree.arrays(entry_start=start, entry_stop=end, how=dict, library="ak")
                for name, array in ordinary.items():
                    assert_exact(ak.to_numpy(ak.num(array)), ak.to_numpy(ak.num(observed[name])), (start, "production length", name))
                    assert_exact(as_numpy(array), as_numpy(observed[name]), (start, "production value", name))
                    production_checks += int(ak.sum(ak.num(array)))
            for name, original_name in EVENT_FIELDS.items():
                assert_exact(ak.to_numpy(observed[name]), ak.to_numpy(raw[original_name]), (start, name))
            multiplicities = ak.to_numpy(ak.num(raw[SOURCE_FIELDS["source_pt"]]))
            update_counts(source_multiplicity, multiplicities)
            n_muons += int(multiplicities.sum())
            for name, original_name in SOURCE_FIELDS.items():
                assert_exact(as_numpy(observed[name]), as_numpy(raw[original_name]), (start, name))
            if stored_file:
                raw_pt = stored_tree["mu_pt_NOSYS"].array(entry_start=start, entry_stop=end, library="ak")
                assert_exact(ak.to_numpy(ak.num(raw_pt)), multiplicities, (start, "stored mode length"))
                values = as_numpy(raw_pt)
                assert_exact(values, as_numpy(raw[SOURCE_FIELDS["source_pt"]]), (start, "stored mode pT"))
                stored_checks += len(values)
                bins = np.searchsorted(np.linspace(0., 500., 101), values.astype(np.float64) / 1000., side="right")
                stored_bin_counts += np.bincount(bins, minlength=102)
            fields = {}
            for name, array in observed.items():
                if name in EVENT_FIELDS:
                    continue
                assert_exact(ak.to_numpy(ak.num(array)), multiplicities, (start, "shape", name))
                branch_checks += len(multiplicities)
                fields[name] = as_numpy(array)
            assert_exact(fields["mu_eta"], fields["source_eta"], (start, "eta"))
            assert_exact(fields["mu_phi"], fields["source_phi"], (start, "phi"))
            update_counts(source_muon_types, fields["source_muonType"])
            runs = ak.to_numpy(observed["runNumber"])
            event_numbers = ak.to_numpy(observed["eventNumber"])
            channels = ak.to_numpy(observed["mcChannelNumber"])
            rrn = ak.to_numpy(observed["randomRunNumber"])
            update_counts(raw_runs, runs)
            update_counts(random_runs, rrn)
            entry = np.arange(start, end, dtype=np.uint64)
            uid = np.stack(tuple(values.astype(np.uint64) for values in (entry, runs, event_numbers, channels)), axis=-1).astype(">u8")
            event_ids.update(uid.tobytes())
            uid = np.repeat(uid, multiplicities, axis=0)
            indices = as_numpy(ak.local_index(observed["source_pt"], axis=-1)).astype(np.uint64)
            identities.update(np.column_stack((uid[:, 0], indices, uid[:, 1:])).astype(">u8").tobytes())

            for state in states:
                evidence = per_state[state]
                def field(prefix):
                    effective = state if state in coverage[prefix] else "NOSYS"
                    return fields[f"{prefix}_{effective}"]

                selected = field("mu_select_medium") != 0
                evidence["selected"] += int(selected.sum())
                eta, pt, track, quality, isolation = (field(f"mu_detail_{name}") for name in GATES)
                staged = (eta == TRACK_ACCEPT) & (pt == TRACK_ACCEPT) & (track == TRACK_ACCEPT) & (quality == 1) & (isolation == 1)
                assert_exact(staged, selected, (start, "selection", state))
                called = eta == TRACK_ACCEPT
                momentum = np.where(called, np.where(field("mu_detail_calibration_valid") == TRACK_ACCEPT, 2, 1), -1)
                assert (momentum != 1).all(), (start, "momentum non-success on abort policy", state)
                update_counts(evidence["momentum_codes"], momentum)
                scales = []
                for stage in TOOLS:
                    prefix = f"mu_{stage}_effSF_medium"
                    weight = field(prefix)
                    status = field(f"mu_detail_{stage}_sf_status")
                    assert np.isin(status[selected], (1, 2)).all(), (start, stage, state, "selected code")
                    assert (status[~selected] == -1).all() and (weight[~selected] == -1).all(), (start, stage, state, "skip sentinel")
                    update_counts(evidence["sf_codes"][stage], status)
                    if state == "NOSYS" and stage == "reco" and len(nominal_reco_oov_examples) < 10:
                        for index in np.flatnonzero(status == 1)[:10 - len(nominal_reco_oov_examples)]:
                            nominal_reco_oov_examples.append({"entry": int(uid[index, 0]), "event_number": int(uid[index, 2]), "original_index": int(indices[index]), "pt_MeV": float(field("mu_pt")[index]), "eta": float(fields["mu_eta"][index]), "muon_type": int(fields["source_muonType"][index]), "sf": float(weight[index])})
                    if state in coverage[prefix]:
                        actual_replay_values[stage] += len(weight)
                    scales.append(weight)
                assert_exact(field("mu_effSF_medium"), (scales[0] * scales[1]) * scales[2], (start, "native float32 product", state))
                for quantity in coverage:
                    array = field(quantity)
                    nominal = fields[f"{quantity}_NOSYS"]
                    evidence["output_changes"][quantity] += int((~(np.equal(array, nominal) | (np.isnan(array) & np.isnan(nominal)))).sum())
                    if array.dtype.kind == "f":
                        evidence["nonfinite_values"][quantity] += int((~np.isfinite(array)).sum())

        if production_file:
            production_file.close()
        if stored_file:
            stored_file.close()
            with np.load(stored / "histogram.npz") as hist:
                assert hist["states"].tolist() == ["NOSYS"]
                for quantity in ("count", "sumw", "sumw2"):
                    assert_exact(hist[quantity][:, 0], stored_bin_counts, ("stored", quantity))

    result = {
        "source_uuid": uuid,
        "source_entries": events,
        "original_muons": n_muons,
        "event_identity_sha256_be_u64_entry_run_event_dsid": event_ids.hexdigest(),
        "object_identity_sha256_be_u64_entry_index_run_event_dsid": identities.hexdigest(),
        "jagged_branch_lengths_checked": branch_checks,
        "observed_branch_count": len(branches),
        "source_run_number_counts": raw_runs,
        "exact_source_random_run_number_counts": random_runs,
        "source_multiplicity": source_multiplicity,
        "source_muon_types": source_muon_types,
        "direct_replay_values_compared_in_cpp": actual_replay_values,
        "production_values_compared": production_checks,
        "stored_values_compared": stored_checks,
        "stored_bins_checked": len(stored_bin_counts) if stored else 0,
        "nominal_reconstruction_out_of_validity_examples": nominal_reco_oov_examples,
        "systematics_order": states,
        "weight_only_order": weight_only,
        "per_state": per_state,
    }
    assert sum(raw_runs.values()) == events and sum(random_runs.values()) == events
    with output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(f"{events} events, {n_muons} original muons, {len(states)} states, {len(branches)} observer branches")
    print(f"{branch_checks} jagged lengths and {sum(actual_replay_values.values())} direct replay values checked")
    print(f"{per_state['NOSYS']['selected']} nominal selected; {production_checks} values compared to ordinary production ROOT")


if __name__ == "__main__":
    typer.run(main)
