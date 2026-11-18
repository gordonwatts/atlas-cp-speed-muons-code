#!/usr/bin/env python3
"""Compare an independently observed replacement with the original release."""

from collections import defaultdict
import json
from pathlib import Path

import awkward as ak
import numpy as np
import typer

from reference_histogram import open_local, systematic_labels


EVENT_FIELDS = {"eventNumber", "runNumber", "mcChannelNumber", "randomRunNumber"}
MOMENTUM = {"mu_pt", "mu_e", "mu_detail_id_pt", "mu_detail_me_pt"}
EFFICIENCY = ("mu_effSF_medium", "mu_reco_effSF_medium", "mu_isol_effSF_medium", "mu_TTVA_effSF_medium")


def flatten(values):
    return ak.to_numpy(ak.flatten(values))


def signature(name, states):
    for state in states:
        suffix = "_" + state
        if name.endswith(suffix):
            return name.removesuffix(suffix), state
    return name, "NOSYS"


def exception_classes(values):
    return np.where(np.isnan(values), 1, np.where(np.isposinf(values), 2, np.where(np.isneginf(values), 3, 0)))


def value_statistics(reference, candidate, tolerance):
    rc, cc = exception_classes(reference), exception_classes(candidate)
    finite = (rc == 0) & (cc == 0)
    both = (rc != 0) & (rc == cc)
    same = (reference == candidate) | both
    diff = np.abs(candidate[finite].astype(np.float64) - reference[finite].astype(np.float64))
    absolute, relative = tolerance
    magnitudes = np.abs(reference[finite].astype(np.float64))
    fail = rc != cc
    if absolute == relative == 0:
        fail |= ~same
    else:
        fail[finite] = diff > absolute + relative * magnitudes
    rel = np.divide(diff, magnitudes, out=np.zeros_like(diff), where=magnitudes != 0)
    return same, fail, float(diff.max(initial=0)), float(rel.max(initial=0))


def compare_histogram(reference, candidate, boundaries, expected_counts, weights, sumw_tolerance, sumw2_tolerance):
    report = {}
    bins = expected_counts.shape
    for label, endpoint in (("A", reference), ("candidate", candidate)):
        if endpoint["count"].shape != bins or not np.array_equal(endpoint["pt_edges_GeV"], boundaries):
            raise ValueError(f"{label} histogram does not have the frozen binning")
    report["reference_count_shape_matches"] = np.array_equal(reference["count"], expected_counts)
    report["count_mismatched_bins"] = int((reference["count"] != candidate["count"]).sum())
    for key, permitted, weight in (("sumw", sumw_tolerance, weights[0]), ("sumw2", sumw2_tolerance, weights[1])):
        eps = np.finfo(np.float64).eps
        gamma = eps * (expected_counts + 2)
        accumulation = 2 * gamma / (1 - gamma) * (weight + permitted)
        difference = np.abs(reference[key] - candidate[key])
        report[key] = {"max_abs": float(difference.max()), "outside_limit": int((~np.isfinite(difference) | (difference > permitted + accumulation)).sum())}
    return report


def main(a: Path, candidate: Path, a_histogram: Path, candidate_histogram: Path, acceptance: Path, output: Path, candidate_systematics: Path | None = None, source_uuid: str = "79ee6652-311f-11f1-aa37-0445010abeef", start: int = 0, stop: int | None = None, chunk_size: int = 1000):
    states, weights = systematic_labels(a / "hist-reference.root")
    numeric_contract = list(json.loads(acceptance.read_text(encoding="utf-8-sig"))["quantities"].values())
    momentum_tolerance, sf_tolerance = [(q["abs_tol"], q["rel_tol"]) for q in numeric_contract]
    metrics = defaultdict(dict)
    first = []
    checks = 0
    source_objects = 0
    shape_errors = []
    extra_status_failures = 0
    bin_difference_counts = defaultdict(int)
    with np.load(a_histogram) as original_histogram:
        edges = original_histogram["pt_edges_GeV"].copy()
    np.testing.assert_allclose(edges, np.linspace(0.0, 500.0, 101), rtol=0, atol=1e-13)
    bins = len(edges) + 1
    expected_counts = np.zeros((bins, len(states)), dtype=np.int64)
    weights_abs = np.zeros((2, bins, len(states)))
    tolerances = np.zeros_like(weights_abs)

    with open_local(a / "reference.root") as reference_file, open_local(candidate) as candidate_file:
        reference, observed = reference_file["analysis"], candidate_file["analysis"]
        if "columnar_metadata" in candidate_file:
            metadata = json.loads(str(candidate_file["columnar_metadata"]))
            assert metadata["source_uuid"] == source_uuid and metadata["entry_start"] == start
            assert (metadata["states"], metadata["weight_only"]) == (states, weights)
            assert metadata["entry_stop"] == start + observed.num_entries
        else:
            assert candidate_systematics is not None and systematic_labels(candidate_systematics) == (states, weights)
        end = reference.num_entries if stop is None else stop
        assert observed.num_entries == end - start
        available = set(observed.keys())
        mapping = {name: name for name in reference.keys() if name in available}
        if "mu_charge" not in mapping and "mu_detail_charge_NOSYS" in available:
            mapping["mu_charge"] = "mu_detail_charge_NOSYS"
        missing = sorted(set(reference.keys()) - mapping.keys())
        optional = {name for name in available if name == "entry" or name == "source_index" or name.startswith(("mu_detail_momentum_", "mu_detail_id_pt_written_", "mu_detail_me_pt_written_", "mu_detail_id_charge_written_", "mu_detail_me_charge_written_", "mu_detail_reco_sf_called_", "mu_detail_isol_sf_called_", "mu_detail_TTVA_sf_called_"))}
        branch_precision = {name: signature(name, states) for name in mapping}

        for offset in range(0, observed.num_entries, chunk_size):
            last = min(offset + chunk_size, observed.num_entries)
            ref = reference.arrays(entry_start=start + offset, entry_stop=start + last, how=dict, library="ak")
            obs = observed.arrays(sorted(set(mapping.values()) | optional), entry_start=offset, entry_stop=last, how=dict, library="ak")
            entries = np.arange(start + offset, start + last, dtype=np.uint64)
            counts = ak.to_numpy(ak.num(ref["source_pt"]))
            left, right = {}, {}
            source_objs = int(counts.sum())
            source_objects += source_objs
            muon_indices = flatten(ak.local_index(ref["source_pt"], axis=1))
            object_entries = np.repeat(entries, counts)
            event_numbers = ak.to_numpy(ref["eventNumber"])
            if "entry" in obs:
                np.testing.assert_array_equal(ak.to_numpy(obs["entry"]), entries)
            if "source_index" in obs:
                np.testing.assert_array_equal(flatten(obs["source_index"]), muon_indices)
            for name, counterpart in mapping.items():
                if name in EVENT_FIELDS:
                    np.testing.assert_array_equal(ak.to_numpy(ref[name]), ak.to_numpy(obs[counterpart]))
                    continue
                shape = ak.to_numpy(ak.num(obs[counterpart]))
                if not np.array_equal(shape, counts):
                    if len(shape_errors) < 20:
                        shape_errors.append({"branch": name, "entry_range_start": int(entries[0])})
                    continue
                orig, new = flatten(ref[name]), flatten(obs[counterpart])
                left[name], right[name] = orig, new
                quantity, state = branch_precision[name]
                tolerance = momentum_tolerance if quantity in MOMENTUM else sf_tolerance if quantity in EFFICIENCY else (0.0, 0.0)
                info = metrics[state].setdefault(quantity, {"compared": 0, "equal_values": 0, "bitwise_equal": 0, "outside_limit": 0, "max_abs": 0.0, "max_relative_nonzero": 0.0})
                same, fail, absdiff, reldiff = value_statistics(orig, new, tolerance)
                info["compared"] += orig.size
                info["equal_values"] += int(same.sum())
                if orig.dtype == new.dtype:
                    dtype = np.dtype(f"u{orig.dtype.itemsize}")
                    info["bitwise_equal"] += int((orig.view(dtype) == new.view(dtype)).sum())
                info["outside_limit"] += int(fail.sum())
                info["max_abs"] = max(absdiff, info["max_abs"])
                info["max_relative_nonzero"] = max(reldiff, info["max_relative_nonzero"])
                checks += orig.size
                if fail.any() and len(first) < 30:
                    i = int(np.flatnonzero(fail)[0])
                    entry = int(object_entries[i])
                    first.append({"branch": name, "entry": entry, "event_number": int(event_numbers[entry - int(entries[0])]), "original_index": int(muon_indices[i]), "reference": str(orig[i]), "candidate": str(new[i])})

            for name in optional - {"entry", "source_index"}:
                quantity, state = signature(name, states)
                if quantity.endswith("sf_called"):
                    kin = "NOSYS" if state in weights else state
                    expected = left[f"mu_select_medium_{kin}"] != 0
                else:
                    called = left[f"mu_detail_eta_gate_{state}"] == 0xFFFFFFFF
                    if quantity.endswith("momentum_status"):
                        expected = np.where(called, np.where(left[f"mu_detail_calibration_valid_{state}"] == 0xFFFFFFFF, 2, 1), -1)
                    elif quantity.endswith("momentum_write_mask"):
                        expected = np.where(called, 63, 0)
                    else:
                        expected = called
                extra_status_failures += int((flatten(obs[name]) != expected).sum())

            for index, state in enumerate(states):
                kin = "NOSYS" if state in weights else state
                momenta = f"mu_pt_{kin}"
                selection = f"mu_select_medium_{kin}"
                combined = f"mu_effSF_medium_{state}"
                if not all(name in left for name in (momenta, selection, combined)):
                    continue
                selected = left[selection] != 0
                transverse = left[momenta][selected].astype(np.float64) / 1000.0
                reference_bins = np.searchsorted(edges, transverse, side="right")
                bins_from_candidate = np.searchsorted(edges, right[momenta][selected].astype(np.float64) / 1000.0, side="right")
                bin_difference_counts[state] += int((reference_bins != bins_from_candidate).sum())
                weight = np.abs(left[combined][selected].astype(np.float64))
                delta = sf_tolerance[0] + sf_tolerance[1] * weight
                expected_counts[:, index] += np.bincount(reference_bins, minlength=bins)
                for group, values in ((weights_abs, (weight, weight * weight)), (tolerances, (delta, delta * (2.0 * weight + delta)))):
                    for selector, values_for_bin in enumerate(values):
                        group[selector, :, index] += np.bincount(reference_bins, weights=values_for_bin, minlength=bins)

    with np.load(a_histogram) as ah, np.load(candidate_histogram) as ch:
        assert ah["states"].tolist() == ch["states"].tolist() == states
        hist = compare_histogram(ah, ch, edges, expected_counts, weights_abs, *tolerances)
    accepted = not (missing or shape_errors or extra_status_failures or sum(bin_difference_counts.values()) or hist["count_mismatched_bins"] or hist["sumw"]["outside_limit"] or hist["sumw2"]["outside_limit"] or not hist["reference_count_shape_matches"] or any(info["outside_limit"] for state in metrics.values() for info in state.values()))
    evidence = {"passed": accepted, "source_uuid": source_uuid, "entry_start": start, "entry_stop": end, "muons": source_objects, "all_states_ordered": states, "values_compared": checks, "missing_branches": missing, "shape_errors": shape_errors, "additional_status_mismatches": extra_status_failures, "per_state": metrics, "first_mismatches": first, "exact_bin_membership_mismatches": bin_difference_counts, "histogram": hist}
    with output.open("x") as handle:
        json.dump(evidence, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(f"Compared {checks} release values on {source_objects} objects across {len(states)} states. Passed: {accepted}")
    if not accepted:
        print(json.dumps({"missing": missing, "shape": shape_errors, "first_mismatches": first[:5], "histogram": hist}, indent=2))
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
