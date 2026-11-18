#!/usr/bin/env python3
"""Extract plot coordinates from the frozen full-file AnalysisBase observer and a candidate."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
from hashlib import file_digest
import json
from pathlib import Path
import sys
from typing import Annotated

import awkward as ak
import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reference_histogram import open_local, systematic_labels


MOMENTUM_TOLERANCE = "transverse momentum (combined, inner detector, extrapolated spectrometer) and energy, MeV"
EFFICIENCY_TOLERANCE = "individual efficiency scale factors and release combined object factor"
ACCEPT = 0xFFFFFFFF
SCHEMA = 1
EVENT_FIELDS = ("eventNumber", "runNumber", "randomRunNumber", "mcChannelNumber")
SOURCE_FIELDS = ("source_pt", "source_eta", "source_phi", "source_muonType")


@dataclass(frozen=True)
class Quantity:
    name: str
    title: str
    branch: str
    mode: str
    axis: tuple[float, float, int] | None = None
    unit: str = ""
    factor: float = 1.0
    tolerance: str | None = None
    population: str = "All input muons"
    mask: str | None = None
    kind: str = ""


QUANTITIES = (
    Quantity("combined_pt", "Combined muon pₜ", "mu_pt", "kin", (0, 500, 100), "GeV", 0.001, MOMENTUM_TOLERANCE),
    Quantity("id_pt", "Inner detector pₜ", "mu_detail_id_pt", "kin", (0, 500, 100), "GeV", 0.001, MOMENTUM_TOLERANCE),
    Quantity("me_pt", "Extrapolated spectrometer pₜ", "mu_detail_me_pt", "kin", (0, 500, 100), "GeV", 0.001, MOMENTUM_TOLERANCE),
    Quantity("energy", "Muon energy", "mu_e", "kin", (0, 1000, 100), "GeV", 0.001, MOMENTUM_TOLERANCE),
    Quantity("eta", "Muon pseudorapidity η", "mu_eta", "static", (-3, 3, 60)),
    Quantity("phi", "Muon azimuth φ", "mu_phi", "static", (-3.2, 3.2, 64), "rad"),
    Quantity("reco_sf", "Reconstruction scale factor", "mu_reco_effSF_medium", "reco", (0, 2.05, 82),
             tolerance=EFFICIENCY_TOLERANCE, population="A reconstruction calls, including OOV", mask="reco_status"),
    Quantity("iso_sf", "Isolation scale factor", "mu_isol_effSF_medium", "iso", (0, 2.05, 82),
             tolerance=EFFICIENCY_TOLERANCE, population="A isolation calls, including OOV", mask="iso_status"),
    Quantity("ttva_sf", "TTVA scale factor", "mu_TTVA_effSF_medium", "ttva", (0, 2.05, 82),
             tolerance=EFFICIENCY_TOLERANCE, population="A TTVA calls, including OOV", mask="ttva_status"),
    Quantity("combined_sf", "Combined object scale factor", "mu_effSF_medium", "all", (0, 2.05, 82),
             tolerance=EFFICIENCY_TOLERANCE, population="Muon selection from A", mask="selected"),
    Quantity("charge", "Muon charge", "mu_detail_charge", "kin", kind="charge"),
    Quantity("id_charge", "Inner detector charge decoration", "mu_detail_id_charge", "kin", kind="charge"),
    Quantity("me_charge", "Extrapolated spectrometer charge decoration", "mu_detail_me_charge", "kin", kind="charge"),
    Quantity("eta_gate", "Raw eta selection", "mu_detail_eta_gate", "kin", kind="bits"),
    Quantity("pt_gate", "Calibrated pₜ selection", "mu_detail_pt_gate", "kin", kind="bits"),
    Quantity("track_gate", "Track selection mask", "mu_detail_track_gate", "kin", kind="track"),
    Quantity("quality_gate", "Medium quality decision", "mu_detail_quality_gate", "kin", kind="bool"),
    Quantity("bad_muon", "Bad-muon flag", "mu_detail_bad_muon", "kin", kind="bool"),
    Quantity("isolation_gate", "PflowTight_VarRad isolation", "mu_detail_isolation_gate", "kin", kind="bool"),
    Quantity("selected", "Final muon selection", "mu_select_medium", "kin", kind="bool"),
    Quantity("calibration_valid", "Calibration validity mask", "mu_detail_calibration_valid", "kin", kind="bits"),
    Quantity("momentum_status", "Calibration call and return status", "", "kin", kind="status"),
    Quantity("reco_status", "Reconstruction return status", "mu_detail_reco_sf_status", "reco", kind="status"),
    Quantity("iso_status", "Isolation return status", "mu_detail_isol_sf_status", "iso", kind="status"),
    Quantity("ttva_status", "TTVA return status", "mu_detail_TTVA_sf_status", "ttva", kind="status"),
)
BY_NAME = {quantity.name: quantity for quantity in QUANTITIES}
SF_ZOOM = (0.90, 1.10, 50)


def source_id(path):
    with path.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest}


def source_state(quantity, state, weights):
    if quantity.mode == "static":
        return "NOSYS"
    if state not in weights or quantity.mode == "all":
        return state
    prefix = {"reco": "MUON_EFF_RECO_", "iso": "MUON_EFF_ISO_", "ttva": "MUON_EFF_TTVA_"}
    if quantity.mode in prefix and state.startswith(prefix[quantity.mode]):
        return state
    return "NOSYS"


def branch_name(quantity, state):
    if quantity.mode == "static":
        return quantity.branch
    return f"{quantity.branch}_{state}"


def flatten(values):
    return ak.to_numpy(ak.flatten(values), allow_missing=False)


def flat_observer(tree, branches, counts):
    columns = tree.arrays(sorted(branches), how=dict, library="ak")
    flat = {}
    for name, column in columns.items():
        if name in EVENT_FIELDS or name == "entry":
            flat[name] = ak.to_numpy(column, allow_missing=False)
        else:
            np.testing.assert_array_equal(ak.to_numpy(ak.num(column)), counts, err_msg=name)
            flat[name] = flatten(column)
    return flat


def load_observers(reference, candidate, candidate_systematics, states, weights, source_uuid, entries, muons):
    kin = [state for state in states if state not in weights]
    names = {branch_name(quantity, source_state(quantity, state, weights))
             for quantity in QUANTITIES if quantity.branch for state in states}
    names.update(EVENT_FIELDS + SOURCE_FIELDS)
    with open_local(reference) as afile, open_local(candidate) as cfile:
        atree, ctree = afile["analysis"], cfile["analysis"]
        if atree.num_entries != entries or ctree.num_entries != entries:
            raise ValueError("Observer trees must cover the full matched event range")
        if candidate_systematics:
            if systematic_labels(candidate_systematics) != (states, weights):
                raise ValueError("Candidate ROOT systematics order differs from the release")
        else:
            metadata = json.loads(str(cfile["columnar_metadata"]))
            if (metadata["source_uuid"], metadata["entry_start"], metadata["entry_stop"],
                    metadata["states"], metadata["weight_only"]) != (source_uuid, 0, entries, states, weights):
                raise ValueError("Candidate observer input identity or state order differs from the release")

        counts = ak.to_numpy(ak.num(atree["source_pt"].array(library="ak")))
        if int(counts.sum()) != muons:
            raise ValueError("Observer muon count differs from the audited release")
        missing_a = names - set(atree.keys())
        missing_c = names - set(ctree.keys())
        if missing_a or missing_c:
            raise ValueError(f"Uncaptured scoped observer quantities: A {sorted(missing_a)}, candidate {sorted(missing_c)}")
        known = set(ctree.keys())
        extra_candidate = ({"entry", "source_index"} | {f"mu_detail_momentum_status_{state}" for state in kin}) & known
        left = flat_observer(atree, names, counts)
        right = flat_observer(ctree, names | extra_candidate, counts)
        for name in EVENT_FIELDS + SOURCE_FIELDS:
            np.testing.assert_array_equal(left[name], right[name], err_msg=name)
        if "entry" in right:
            np.testing.assert_array_equal(right["entry"], np.arange(entries))
        if "source_index" in right:
            indices = np.arange(muons) - np.repeat(np.cumsum(counts) - counts, counts)
            np.testing.assert_array_equal(right["source_index"], indices)
        return left, right


def values(columns, quantity, actual_state, candidate):
    if quantity.name == "momentum_status":
        branch = f"mu_detail_momentum_status_{actual_state}"
        if candidate and branch in columns:
            return columns[branch]
        called = columns[f"mu_detail_eta_gate_{actual_state}"] == ACCEPT
        valid = columns[f"mu_detail_calibration_valid_{actual_state}"] == ACCEPT
        return np.where(called, np.where(valid, 2, 1), -1).astype(np.int8)
    return columns[branch_name(quantity, actual_state)]


def axis_edges(axis):
    low, high, bins = axis
    return np.linspace(low, high, bins + 1)


def bin_indices(edges, vals):
    return np.searchsorted(edges, vals, side="right")


def spectrum(edges, vals):
    finite = vals[np.isfinite(vals)]
    return np.bincount(bin_indices(edges, finite), minlength=len(edges) + 1).tolist()


def exception_code(values):
    return np.where(np.isnan(values), 1, np.where(np.isposinf(values), 2, np.where(np.isneginf(values), 3, 0)))


def bitwise_count(a, b):
    left_type, right_type = a.dtype, b.dtype
    if left_type.newbyteorder("=") != right_type.newbyteorder("="):
        return None
    left = a.astype(left_type.newbyteorder("="), copy=False)
    right = b.astype(right_type.newbyteorder("="), copy=False)
    size = left_type.itemsize
    return int(np.count_nonzero(left.view(f"u{size}") == right.view(f"u{size}")))


def distribution_edges(quantity):
    axes = {"full": axis_edges(quantity.axis)}
    if quantity.tolerance == EFFICIENCY_TOLERANCE:
        axes["core"] = axis_edges(SF_ZOOM)
    return axes


def pair_counts(values):
    finite = values[np.isfinite(values)]
    counts = {}
    for key, n in zip(*np.unique(finite, return_counts=True), strict=True):
        value = key.item()
        label = str(int(value)) if value == int(value) else repr(value)
        counts[label] = int(n)
    for name, code in (("NaN", 1), ("+inf", 2), ("-inf", 3)):
        tally = int(np.count_nonzero(exception_code(values) == code))
        if tally:
            counts[name] = tally
    if sum(counts.values()) != len(values):
        raise ValueError("Category keys did not preserve every observed row")
    return counts


def compare_values(a, c, tolerance):
    aa, cc = exception_code(a), exception_code(c)
    finite = (aa == 0) & (cc == 0)
    a64, c64 = a[finite].astype(np.float64), c[finite].astype(np.float64)
    differences = c64 - a64
    magnitudes = np.abs(differences)
    width = tolerance[0] + tolerance[1] * np.abs(a64)
    perm = np.divide(magnitudes, width, out=np.zeros_like(magnitudes), where=width > 0)
    bad_classes = int(np.count_nonzero(aa != cc))
    details = {
        "pairs": int(a.size),
        "finite_pairs": int(np.count_nonzero(finite)),
        "equal_finite": int(np.count_nonzero(differences == 0)),
        "equal_stored_bits": bitwise_count(a, c),
        "nonfinite_A": np.bincount(aa, minlength=4)[1:].tolist(),
        "nonfinite_candidate": np.bincount(cc, minlength=4)[1:].tolist(),
        "nonfinite_class_mismatches": bad_classes,
        "failed": bad_classes + int(np.count_nonzero(magnitudes > width)),
        "max_abs": float(magnitudes.max(initial=0.0)),
        "min_signed": float(differences.min()) if differences.size else None,
        "max_signed": float(differences.max()) if differences.size else None,
        "max_tolerance_fraction": float(perm.max(initial=0.0)) if width.size and np.all(width > 0) else None,
    }
    for values, codes, label in ((a, aa, "A"), (c, cc, "candidate")):
        finite_values = values[codes == 0]
        details[f"{label}_finite_min"] = float(finite_values.min()) if finite_values.size else None
        details[f"{label}_finite_max"] = float(finite_values.max()) if finite_values.size else None
    return finite, differences, details


def binned_pair(edges, left, right, a_pair, differences):
    bins = bin_indices(edges, a_pair)
    number = len(edges) + 1
    low, high = np.full(number, np.inf), np.full(number, -np.inf)
    np.minimum.at(low, bins, differences)
    np.maximum.at(high, bins, differences)
    return {
        "A": spectrum(edges, left),
        "candidate": spectrum(edges, right),
        "paired_count": np.bincount(bins, minlength=number).tolist(),
        "min_signed": [float(v) if np.isfinite(v) else None for v in low],
        "max_signed": [float(v) if np.isfinite(v) else None for v in high],
    }


def output_quantity(quantity, left, right, states, weights, tolerances):
    axes = distribution_edges(quantity) if quantity.axis else {}
    cache = {}
    effective_states = {}
    tolerance = tolerances[quantity.tolerance] if quantity.tolerance else (0.0, 0.0)
    for state in states:
        actual = source_state(quantity, state, weights)
        effective_states[state] = actual
        # A combined factor is evaluated for every state even when its selection aliases nominal.
        mask_state = "NOSYS" if state in weights else state
        if actual in cache:
            continue
        a = values(left, quantity, actual, False)
        c = values(right, quantity, actual, True)
        population = {}
        if quantity.mask:
            mask_var = BY_NAME[quantity.mask]
            source = source_state(mask_var, mask_state if quantity.mask == "selected" else state, weights)
            codes = values(left, mask_var, source, False)
            used = codes != (0 if mask_var.name == "selected" else -1)
            population["excluded_A"] = int((~used).sum())
            if mask_var.kind == "status":
                observed = values(right, mask_var, source, True)
                population["A_oov"] = int((codes == 1).sum())
                population["candidate_oov"] = int((observed == 1).sum())
            a, c = a[used], c[used]
        finite, difference, summary = compare_values(a, c, tolerance)
        for key in ("max_abs", "min_signed", "max_signed", "A_finite_min", "A_finite_max",
                    "candidate_finite_min", "candidate_finite_max"):
            if summary[key] is not None:
                summary[key] *= quantity.factor
        payload = {"summary": summary, **population}
        if axes:
            a_display = a.astype(np.float64) * quantity.factor
            c_display = c.astype(np.float64) * quantity.factor
            payload["plots"] = {
                key: binned_pair(edges, a_display, c_display,
                                 a_display[finite], difference * quantity.factor)
                for key, edges in axes.items()
            }
        else:
            payload["categories"] = {"A": pair_counts(a), "candidate": pair_counts(c)}
        cache[actual] = payload
    return {
        "title": quantity.title, "branch": quantity.branch, "unit": quantity.unit,
        "axis": {key: value.tolist() for key, value in axes.items()}, "population": quantity.population,
        "mode": quantity.mode, "kind": quantity.kind, "effective_state": effective_states, "records": cache,
    }


def near_threshold(left, right, states, weights):
    configurations = {
        "pt": ((2.5, 3.5, 40), 3.0, "Calibrated pₜ [GeV]", "mu_detail_pt_gate"),
        "eta": ((2.60, 2.80, 40), float(np.float32(2.7)), "Stored |η|", "mu_detail_eta_gate"),
    }
    output = {}
    for key, (axis, cut, label, branch) in configurations.items():
        edges = axis_edges(axis)
        records = {}
        for state in states:
            if state in weights or (key == "eta" and state != "NOSYS"):
                continue
            mask = left[f"mu_detail_eta_gate_{state}"] == ACCEPT if key == "pt" else slice(None)
            coordinate_A = (left[f"mu_pt_{state}"].astype(np.float64) / 1000 if key == "pt"
                            else np.abs(left["source_eta"].astype(np.float64)))
            coordinate_C = (right[f"mu_pt_{state}"].astype(np.float64) / 1000 if key == "pt"
                            else np.abs(right["source_eta"].astype(np.float64)))
            gate = [columns[f"{branch}_{state}"][mask] == ACCEPT for columns in (left, right)]
            records[state] = {
                "A_pass": spectrum(edges, coordinate_A[mask][gate[0]]),
                "A_fail": spectrum(edges, coordinate_A[mask][~gate[0]]),
                "candidate_pass": spectrum(edges, coordinate_C[mask][gate[1]]),
                "candidate_fail": spectrum(edges, coordinate_C[mask][~gate[1]]),
                "paired_changed_gates": int((gate[0] != gate[1]).sum()),
            }
        output[key] = {"edges": edges.tolist(), "cut": cut, "axis_label": label, "records": records}
    return output


def endpoint(a_path, c_path, states):
    with np.load(a_path, allow_pickle=False) as a, np.load(c_path, allow_pickle=False) as c:
        edges = a["pt_edges_GeV"]
        np.testing.assert_allclose(edges, np.linspace(0, 500, 101), rtol=0, atol=1e-13)
        np.testing.assert_array_equal(edges, c["pt_edges_GeV"])
        for data in (a, c):
            if data["states"].tolist() != states:
                raise ValueError("Endpoint systematics do not match the observer order")
            if any(data[field].shape != (len(edges) + 1, len(states)) for field in ("count", "sumw", "sumw2")):
                raise ValueError("Endpoint histogram is missing bins or flow rows")
        records = {
            state: {
                label: {field: data[field][:, index].tolist() for field in ("count", "sumw", "sumw2")}
                for label, data in (("A", a), ("candidate", c))
            } for index, state in enumerate(states)
        }
        return {"edges": edges.tolist(), "records": records}


def main(
    reference: Annotated[Path, typer.Option(help="Full Path A observer analysis ROOT file.")],
    reference_systematics: Annotated[Path, typer.Option(help="Path A hist-reference.root with ordered state labels.")],
    reference_audit: Annotated[Path, typer.Option(help="Full independent original-file observer audit JSON.")],
    reference_histogram: Annotated[Path, typer.Option(help="Actual full Path A output histogram NPZ.")],
    candidate: Annotated[Path, typer.Option(help="Full B or C observer ROOT file.")],
    candidate_histogram: Annotated[Path, typer.Option(help="Actual full B or C output histogram NPZ.")],
    comparison: Annotated[Path, typer.Option(help="The full paired compare_reference.py result for this candidate.")],
    acceptance: Annotated[Path, typer.Option(help="Campaign frozen acceptance JSON.")],
    path_label: Annotated[str, typer.Option(help="B for framework or C for columnar.")],
    output: Annotated[Path, typer.Option(help="New .json.gz plot-data file.")],
    candidate_systematics: Annotated[Path | None, typer.Option(help="Required for B; C stores metadata in its observer.")] = None,
    implementation_evidence: Annotated[list[Path] | None, typer.Option(
        help="Repeat for loaded-runtime identity, artifact hash or build receipt files."
    )] = None,
):
    """Extract distributions and paired residuals once, outside production timers."""
    if path_label not in {"B", "C"} or (path_label == "B" and candidate_systematics is None):
        raise typer.BadParameter("Supply B with --candidate-systematics or C with embedded metadata")
    if not output.name.endswith(".json.gz") or output.exists():
        raise typer.BadParameter("Supply a fresh output path ending in .json.gz")
    audit = json.loads(reference_audit.read_text(encoding="utf-8-sig"))
    compare = json.loads(comparison.read_text(encoding="utf-8-sig"))
    states, weights = systematic_labels(reference_systematics)
    entries = audit["source_entries"]
    muons = audit["original_muons"]
    identity = audit["source_uuid"]
    if (entries, muons, len(states), len(weights)) != (20_000, 14_997, 97, 68):
        raise ValueError("Expected the full frozen sample and the 97-state inventory")
    if (audit["systematics_order"], audit["weight_only_order"]) != (states, weights):
        raise ValueError("The original audit and ROOT state labels disagree")
    if (compare["source_uuid"], compare["entry_start"], compare["entry_stop"], compare["muons"],
            compare["all_states_ordered"]) != (identity, 0, entries, muons, states):
        raise ValueError("Comparison JSON does not cover these objects and all states")
    if compare["missing_branches"] or compare["shape_errors"]:
        raise ValueError("Resolve missing branches or jagged shape disagreements before plotting")
    contract = json.loads(acceptance.read_text(encoding="utf-8-sig"))
    tolerances = {name: (row["abs_tol"], row["rel_tol"]) for name, row in contract["quantities"].items()}
    left, right = load_observers(reference, candidate, candidate_systematics, states, weights, identity, entries, muons)
    payload = {
        "schema_version": SCHEMA, "candidate": path_label, "accepted": bool(compare["passed"]),
        "source_uuid": identity, "entry_range": [0, entries], "muons": muons,
        "object_identity_sha256": audit["object_identity_sha256_be_u64_entry_index_run_event_dsid"],
        "states": states, "weight_only": weights, "tolerances": tolerances,
        "provenance": {key: source_id(path) for key, path in {
            "reference": reference, "reference_systematics": reference_systematics,
            "reference_audit": reference_audit, "reference_histogram": reference_histogram,
            "candidate": candidate, "candidate_histogram": candidate_histogram,
            "comparison": comparison, "acceptance": acceptance, "extractor": Path(__file__),
            **({"candidate_systematics": candidate_systematics} if candidate_systematics else {}),
        }.items()},
        "implementation_evidence": [source_id(path) for path in implementation_evidence or ()],
        "quantities": {quantity.name: output_quantity(quantity, left, right, states, set(weights), tolerances)
                       for quantity in QUANTITIES},
        "thresholds": near_threshold(left, right, states, set(weights)),
        "endpoint": endpoint(reference_histogram, candidate_histogram, states),
        "comparison_endpoint": compare["histogram"],
        "comparison_membership_mismatches": compare["exact_bin_membership_mismatches"],
    }
    if payload["accepted"] and any(
        row["summary"]["failed"] for quantity in payload["quantities"].values()
        for row in quantity["records"].values()
    ):
        raise ValueError("Plotted object values disagree with the supplied acceptance record")
    with gzip.open(output, "xt", encoding="utf-8") as handle:
        json.dump(payload, handle, allow_nan=False, separators=(",", ":"))
        handle.write("\n")
    print(f"Extracted {len(QUANTITIES)} quantities across {len(states)} states for A vs {path_label}")


if __name__ == "__main__":
    typer.run(main)
