#!/usr/bin/env python3
"""Export the frozen MC23a muon efficiency prescription from resolver ROOT files."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
import math
from pathlib import Path
import struct
from typing import Annotated

import typer


ATHENA_COMMIT = "6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c"
CORE_COMMIT = "3dcfa20ec01fb40c4d5fec58383785519dec1c00"
CALIBRATION = "251211_Preliminary_r24run3"
COLLECTIONS = {
    "reco": {
        "central": "Reco_Medium_Z.root",
        "calo": "Reco_CaloTag_Z.root",
        "forward": "Reco_HighEta_Z.root",
        "central_lowpt": "Reco_Medium_JPsi.root",
        "calo_lowpt": "Reco_CaloTag_JPsi.root",
    },
    "iso": {"central": "Iso_PflowTight_VarRadIso_Z.root"},
    "ttva": {"central": "TTVA_Z.root"},
}
DISCOVERY_LABEL = {"reco": "reco", "iso": "isolation", "ttva": "ttva"}
WORKING_POINTS = {"reco": "Medium", "iso": "PflowTight_VarRadIso", "ttva": "TTVA"}
STATUS = {"skipped": -1, "Error": 0, "OutOfValidityRange": 1, "Ok": 2}
INPUTS = [
    ("state", "string"), ("random_run_number", "int"), ("pt", "real"),
    ("eta", "real"), ("phi", "real"), ("charge", "real"),
    ("muon_type", "int"), ("gate", "int"),
]
PRODUCT_INPUTS = [("reco_sf", "real"), ("iso_sf", "real"), ("ttva_sf", "real")]


def extension(node_type, config=None, children=(), *, array=False):
    output = {
        "nodetype": "extension",
        "type": "atlas.muon_eff." + node_type,
        "version": 1,
        "config": {} if config is None else config,
    }
    if children:
        output["children"] = list(children)
    if array:
        output["output_shape"] = [2]
    return output


def category(input_name, entries, fallback=None):
    output = {
        "nodetype": "category", "input": input_name,
        "content": [{"key": key, "value": value} for key, value in entries.items()],
    }
    if fallback is not None:
        output["default"] = fallback
    return output


def transform(input_name, rule, node):
    return {"nodetype": "transform", "input": input_name, "rule": rule, "content": node}


def correction(name, inputs, data, output_name, *, array=False):
    output = {"name": output_name, "type": "real"}
    if array:
        output["shape"] = [2]
    return {
        "name": name,
        "version": 1,
        "inputs": [{"name": key, "type": kind} for key, kind in inputs],
        "output": output,
        "data": data,
    }


def float32(value):
    return struct.unpack(">f", struct.pack(">f", value))[0]


def ordered_bits(value):
    bits = struct.unpack(">I", struct.pack(">f", value))[0]
    return bits ^ (0xFFFFFFFF if bits & 0x80000000 else 0x80000000)


def ordered_float(bits):
    original = bits ^ (0x80000000 if bits & 0x80000000 else 0xFFFFFFFF)
    return struct.unpack(">f", struct.pack(">I", original))[0]


def axis_separator(axis, bin_number):
    """Place the edge between adjacent float32 coordinates on opposite sides."""
    low = ordered_bits(float("-inf"))
    high = ordered_bits(float("inf"))
    while high > low:
        middle = (low + high) // 2
        if axis.FindFixBin(ordered_float(middle)) < bin_number:
            low = middle + 1
        else:
            high = middle
    lower, upper = ordered_float(low - 1), ordered_float(low)
    return (lower + upper) / 2.


def axis_spec(axis):
    # UtilFunctions::EraseWhiteSpaces drops tabs and leading spaces.
    title = str(axis.GetTitle()).replace("\t", "").lstrip(" ")
    if any(token in title for token in ("pt", "pT", "p_{T}")):
        return "pt"
    if "phi" in title:
        return "phi"
    if "q" in title or "charge" in title:
        return "charge"
    if "eta" in title:
        return "abs_eta" if title.count("|") > 1 else "eta"
    if "dRJet" in title or "#DeltaR (jet, #mu)" in title:
        return "dr_jet"
    raise ValueError(f"Unknown source axis {axis.GetTitle()!r}")


def axis_transforms(node, names):
    for name in names:
        if name == "pt":
            node = transform("pt", extension("pt_gev"), node)
        elif name == "abs_eta":
            rule = {"nodetype": "formula", "expression": "abs(x)",
                    "parser": "TFormula", "variables": ["eta"]}
            node = transform("eta", rule, node)
    return node


def axis_input(name):
    return "eta" if name == "abs_eta" else name


@dataclass(frozen=True)
class MapFile:
    root: object
    source: dict
    metadata: dict

    def histogram(self, name, period):
        for key in ((name + "_" + period, name) if period == "All" else (name + "_" + period,)):
            histogram = self.root.Get(key)
            if histogram and histogram.InheritsFrom("TH1"):
                return histogram, key
        return None, None


@dataclass(frozen=True)
class Systematic:
    maps: frozenset[str]
    symmetric: bool = False
    kinematic: bool = False

    def union(self, other):
        return Systematic(self.maps | other.maps, self.symmetric or other.symmetric,
                          self.kinematic or other.kinematic)


def systematics(measurement, files):
    components = {}
    for collection, file in files.items():
        here = frozenset({collection})
        components["STAT"] = components.get("STAT", Systematic(frozenset())).union(Systematic(here))
        tree = file.root.Get("Systematics")
        if not tree:
            rows = [{"name": "SYS", "symmetric": False, "kinematic": measurement == "reco",
                     "can_be_uncorrelated": False}]
        else:
            branches = ("Name", "IsSymmetric", "HasPtDependentSys", "CanBeUncorrelated")
            if not tree.InheritsFrom("TTree") or any(not tree.GetBranch(key) for key in branches):
                raise ValueError(f"Unexpected Systematics schema in {file.source['relative']}")
            rows = [
                {"name": str(row.Name), "symmetric": bool(row.IsSymmetric),
                 "kinematic": bool(row.HasPtDependentSys), "can_be_uncorrelated": bool(row.CanBeUncorrelated)}
                for row in tree
            ]
        file.metadata["systematics"] = rows
        for row in rows:
            piece = Systematic(here, row["symmetric"], row["kinematic"])
            name = row["name"]
            components[name] = components.get(name, Systematic(frozenset())).union(piece)

    variations = {}
    for name, component in sorted(components.items()):
        for low_pt in (False, True):
            maps = frozenset(key for key in component.maps if key.endswith("_lowpt") == low_pt)
            if not maps:
                continue
            stem = f"MUON_EFF_{measurement.upper()}_{name}" + ("_LOWPT" if low_pt else "")
            for sign, suffix in ((-1, "__1down"), (1, "__1up")):
                variations[stem + suffix] = {
                    "name": name, "sign": sign, "symmetric": component.symmetric,
                    "kinematic": component.kinematic, "maps": maps,
                }
    return variations


def periods(file):
    tree = file.root.Get("LumiData")
    if not tree:
        return [("All", 1, 999999)]
    branches = ("Period", "FirstRun", "LastRun")
    if not tree.InheritsFrom("TTree") or any(not tree.GetBranch(key) for key in branches):
        raise ValueError(f"Unexpected LumiData schema in {file.source['relative']}")
    period_map = {str(row.Period): (int(row.FirstRun), int(row.LastRun)) for row in tree}
    result = sorted(((name, *bounds) for name, bounds in period_map.items()), key=lambda row: row[1])
    if not result or any(first > last for _, first, last in result):
        raise ValueError(f"Empty or invalid run periods in {file.source['relative']}")
    if any(left[2] >= right[1] for left, right in zip(result, result[1:])):
        raise ValueError(f"Overlapping run periods in {file.source['relative']}")
    return result


def period_node(rows):
    if len(rows) == 1:
        return rows[0][2]
    fallback = rows[-1][2]
    edges = [rows[0][0]]
    contents = []
    for first, last, node in rows:
        if first != edges[-1]:
            edges.append(first)
            contents.append(fallback)
        edges.append(last + 1)
        contents.append(node)
    return {"nodetype": "binning", "input": "random_run_number", "edges": [float(edge) for edge in edges],
            "content": contents, "flow": fallback}


def histogram_node(hist, value):
    if hist.ClassName() not in ("TH1F", "TH2F", "TH2D"):
        raise ValueError(f"Unsupported active histogram class {hist.ClassName()} in {hist.GetName()}")
    dimension = int(hist.GetDimension())
    axes = (hist.GetXaxis(), hist.GetYaxis())[:dimension]
    if any(axis.CanExtend() and not axis.IsAlphanumeric() for axis in axes):
        raise ValueError(f"Cannot export an extensible numeric axis in {hist.GetName()}")
    names = [axis_spec(axis) for axis in axes]
    inputs = [axis_input(name) for name in names if name != "dr_jet"]
    if len(inputs) != len(set(inputs)):
        raise ValueError(f"Repeated dependent histogram axes in {hist.GetName()}")

    edge_sets = []
    indices = []
    axes_provenance = []
    for name, axis in zip(names, axes, strict=True):
        count = int(axis.GetNbins())
        original = [float(axis.GetBinLowEdge(index)) for index in range(1, count + 2)]
        variable = axis.GetXbins().GetSize() != 0
        axes_provenance.append({"title": str(axis.GetTitle()), "coordinate": name,
                                "source_edges": original, "root_variable_binning": variable})
        if name == "dr_jet":
            # Use2DIsoCorrections is false in the pinned configuration.
            fixed_bin = axis.FindFixBin(-2.)
            if fixed_bin not in range(1, count + 1):
                return extension("missing_bin"), axes_provenance
            axes_provenance[-1]["selected_source_bin"] = fixed_bin
            indices.append((fixed_bin,))
        else:
            # Retain the exact ROOT partition on reachable float32 inputs even
            # at edges and through double parsing in the standalone evaluator.
            edges = [axis_separator(axis, index) for index in range(1, count + 2)]
            if any(not math.isfinite(edge) for edge in edges) or any(
                left >= right for left, right in zip(edges, edges[1:])
            ):
                raise ValueError(f"Invalid reachable edges in {hist.GetName()}: {axis.GetTitle()}")
            edge_sets.append(edges)
            axes_provenance[-1]["payload_edges"] = edges
            indices.append(range(1, count + 1))

    contents = [value(hist.GetBin(*bins)) for bins in product(*indices)]
    if any(not math.isfinite(item) for item in contents):
        raise ValueError(f"Nonfinite source contents in {hist.GetName()}")
    fallback = extension("missing_bin")
    if len(inputs) > 1:
        node = {"nodetype": "multibinning", "inputs": inputs, "edges": edge_sets,
                "content": contents, "flow": fallback}
    elif inputs:
        node = {"nodetype": "binning", "input": inputs[0], "edges": edge_sets[0],
                "content": contents, "flow": fallback}
    else:
        node = contents[0]
    return axis_transforms(node, names), axes_provenance


def map_result(file, collection, measurement, period, variation, provenance):
    nominal, nominal_key = file.histogram("SF", period)
    if nominal is None:
        raise ValueError(f"Missing nominal SF in {file.source['relative']} period {period}")

    def nominal_content(index):
        return float(nominal.GetBinContent(index))

    sign = 0 if variation is None else variation["sign"]

    def nominal_sf(index):
        return float32(nominal_content(index))

    value = nominal_sf
    descriptor = {"nominal": nominal_key, "class": str(nominal.ClassName()), "sign": sign}
    if variation is not None:
        name = variation["name"]
        direction = "1UP" if sign > 0 else "1DN"
        ending = "SYM" if variation["symmetric"] else direction
        delta, delta_key = file.histogram(f"SF_{name}_{ending}", period)
        if delta is None and name == "SYS":
            delta, delta_key = file.histogram("SF_sys", period)
        if delta is not None:
            # The release subtracts the stored DOWN map content, regardless of its sign.
            def shifted_content(index):
                return float32(nominal_content(index) + sign * delta.GetBinContent(index))

            value = shifted_content
            descriptor["variation_key"] = delta_key
        elif name == "STAT":
            def stat_content(index):
                return float32(nominal_content(index) + sign * nominal.GetBinError(index))

            value = stat_content
            descriptor["variation_key"] = nominal_key + " GetBinError"
        elif name != "PTDEPENDENCY":
            raise ValueError(f"Missing {name} {ending} SF in {file.source['relative']} period {period}")

    raw, axes = histogram_node(nominal, value)
    children = [raw]
    descriptor["axes"] = axes
    mode = "none"
    if variation and variation["kinematic"] and collection == "central":
        if measurement == "ttva":
            closure, closure_key = file.histogram("SF_NonClosure_sys", period)
            if closure is None:
                raise ValueError(f"Missing TTVA closure SF in {file.source['relative']}")
            mode = "closure"
            closure_node, closure_axes = histogram_node(closure, closure.GetBinContent)
            children.append(closure_node)
            descriptor["kinematic_keys"] = [closure_key]
            descriptor["kinematic_axes"] = {closure_key: closure_axes}
        else:
            loss, loss_key = file.histogram("SF_PtDep_sys", period)
            if loss is None:
                raise ValueError(f"Missing energy-loss systematic in {file.source['relative']}")
            flat, flat_key = file.histogram(f"SF_PtFlatness_{direction}", period)
            if flat is None:
                mode = "primordial"
                descriptor["kinematic_keys"] = [loss_key]
                descriptor["kinematic_axes"] = {}
            else:
                mode = "flatness"
                flat_node, flat_axes = histogram_node(flat, flat.GetBinContent)
                children.extend((flat_node, histogram_node(flat, flat.GetBinError)[0]))
                descriptor["kinematic_keys"] = [flat_key, flat_key + " GetBinError", loss_key]
                descriptor["kinematic_axes"] = {flat_key: flat_axes}
            loss_node, loss_axes = histogram_node(loss, loss.GetBinContent)
            children.append(loss_node)
            descriptor["kinematic_axes"][loss_key] = loss_axes
    descriptor["kinematic"] = mode
    provenance.update(descriptor)
    return extension(
        "map_result",
        {"is_ttva": int(measurement == "ttva"), "fallback": float(1 + sign),
         "forward_sf": float32(1 + sign * 1.e-9), "sign": sign, "kinematic": mode},
        children, array=True,
    )


def tool_correction(measurement, files, variations, states):
    maps = []
    provenance = []
    for collection, file in files.items():
        run_periods = periods(file)
        file.metadata["run_periods"] = [
            {"period": label, "first_run": first, "last_run": last} for label, first, last in run_periods
        ]
        file.metadata["period_fallback"] = run_periods[-1][0]
        entries = {}
        for state in states:
            variation = variations.get(state)
            if state != "NOSYS" and (variation is None or collection not in variation["maps"]):
                continue
            rows = []
            for period, first, last in run_periods:
                record = {"state": state, "collection": collection, "source": file.source["relative"],
                          "sha256": file.source["sha256"], "period": period}
                node = map_result(file, collection, measurement, period, variation, record)
                rows.append((first, last, node))
                provenance.append(record)
            entries[state] = period_node(rows)
        nominal = entries.pop("NOSYS")
        maps.append(category("state", entries, nominal) if entries else nominal)
    allowlist = category("state", dict.fromkeys(states, 1.))
    nodes = extension(
        "tool", {"measurement": measurement, "low_pt_mev": 10000. if measurement == "reco" else -1.,
                 "forward_eta": 2.5, "calo_type": 3, "skip_sf": -1.},
        [allowlist, *maps], array=True,
    )
    return correction("muon_eff_" + measurement, INPUTS, nodes, "sf_and_correction_code", array=True), provenance


def validate_states(states, discovery):
    reference_states = set().union(*(set(item["recommended"]) for item in discovery.values()))
    if len(states) != 97 or states[0] != "NOSYS" or len(states) != len(set(states)):
        raise ValueError("Expected the frozen ordered 97-state list with NOSYS first")
    if set(states[1:]) != reference_states:
        raise ValueError("Ordered states disagree with the initialized tool inventories")
    for label, count in (("momentum", 28), ("reco", 38), ("isolation", 18), ("ttva", 12)):
        if len(discovery[label]["recommended"]) != count:
            raise ValueError(f"Unexpected initialized systematic count for {label}")


def input_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    relevant = [row for row in manifest if row["relative"].startswith("MuonEfficiencyCorrections/")]
    expected = {f"MuonEfficiencyCorrections/{CALIBRATION}/{name}"
                for files in COLLECTIONS.values() for name in files.values()}
    if len(relevant) != len(expected) or {row["relative"] for row in relevant} != expected:
        raise ValueError("Resolved efficiency data disagree with the seven release maps")
    return {Path(row["relative"]).name: row for row in relevant}


def open_map(root, source):
    path = Path(source["resolved"])
    if path.resolve(strict=True) != path:
        raise ValueError(f"Manifest resolved path changed for {source['relative']}")
    content = path.read_bytes()
    if len(content) != source["size"] or sha256(content).hexdigest() != source["sha256"]:
        raise ValueError(f"Manifest source digest changed for {source['relative']}")
    file = root.TFile.Open(str(path), "READ")
    if not file or file.IsZombie():
        raise ValueError(f"Failed to open {path}")
    metadata = {"relative": source["relative"], "resolved": str(path),
                "size": source["size"], "sha256": source["sha256"]}
    return MapFile(file, source, metadata)


def json_output(path, payload):
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")
    return sha256(path.read_bytes()).hexdigest()


def export(
    resolved_manifest: Annotated[Path, typer.Argument(help="Checkpoint 01 resolved-calibration-data.json")],
    initialized_tools: Annotated[Path, typer.Argument(help="Checkpoint 01 initialized-tools-allsys-eta.json")],
    smoke_evidence: Annotated[Path, typer.Argument(help="Checkpoint 01 all-systematics smoke-evidence.json")],
    payload: Annotated[Path, typer.Argument(help="Output schema-v3 correction set JSON")],
    provenance: Annotated[Path, typer.Argument(help="Output source-to-node provenance JSON")],
):
    """Read the release ROOT files using PyROOT in the pinned AnalysisBase environment."""
    import ROOT

    ROOT.PyConfig.IgnoreCommandLineOptions = True
    source_by_name = input_manifest(resolved_manifest)
    discovery = json.loads(initialized_tools.read_text(encoding="utf-8-sig"))
    states = json.loads(smoke_evidence.read_text(encoding="utf-8-sig"))["states"]["all_ordered"]
    validate_states(states, discovery)
    corrections = []
    source_files = []
    map_records = {}
    for measurement, collections in COLLECTIONS.items():
        tool = discovery[DISCOVERY_LABEL[measurement]]
        settings = tool["properties_overridden"]
        if settings != {"WorkingPoint": WORKING_POINTS[measurement], "CalibrationRelease": CALIBRATION,
                        "BreakDownSystematics": True, "UncorrelateSystematics": False}:
            raise ValueError(f"Unexpected initialized efficiency settings for {measurement}: {settings}")
        files = {collection: open_map(ROOT, source_by_name[name])
                 for collection, name in collections.items()}
        try:
            variations = systematics(measurement, files)
            if set(variations) != set(tool["recommended"]):
                unexpected = sorted(set(variations) - set(tool["recommended"]))
                absent = sorted(set(tool["recommended"]) - set(variations))
                raise ValueError(f"Variation inventory mismatch for {measurement}: {unexpected=}, {absent=}")
            graph, records = tool_correction(measurement, files, variations, states)
            corrections.append(graph)
            source_files.extend(file.metadata for file in files.values())
            map_records[graph["name"]] = records
        finally:
            for file in files.values():
                file.root.Close()
    corrections.append(correction("muon_eff_product", PRODUCT_INPUTS, extension("float_product"), "sf"))
    artifact = {"schema_version": 3, "description": "ATLAS MC23a Medium/PflowTight_VarRad/track efficiency",
                "corrections": corrections}
    digest = json_output(payload, artifact)
    json_output(provenance, {
        "athena_release": "AnalysisBase,25.2.117",
        "athena_commit": ATHENA_COMMIT,
        "correctionlib_commit": CORE_COMMIT,
        "root_version": str(ROOT.gROOT.GetVersion()),
        "calibration": CALIBRATION,
        "configuration": {"break_down_systematics": True, "uncorrelate_systematics": False,
                          "apply_kinematic_systematic": True, "use_2d_iso_corrections": False, "use_lrt": False},
        "payload_sha256": digest,
        "input_evidence_sha256": {path.name: sha256(path.read_bytes()).hexdigest() for path in
                                  (resolved_manifest, initialized_tools, smoke_evidence)},
        "states_ordered": states,
        "status_codes": STATUS,
        "files": source_files,
        "map_records": map_records,
    })


if __name__ == "__main__":
    typer.run(export)
