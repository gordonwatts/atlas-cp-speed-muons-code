#!/usr/bin/env python3
"""Exercise the shared ROOT-free stages against release data and boundary fixtures."""

import ctypes
import hashlib
import json
from pathlib import Path
import sys

import awkward as ak
from correctionlib import _core, plugins
import correctionlib
import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/muon_reference"))
from reference_histogram import open_local, systematic_labels


def loaded_abi(provider):
    library = ctypes.CDLL(str(provider.resolve()))
    library.correctionlib_plugin_build_id.restype = ctypes.c_char_p
    version = library.correctionlib_plugin_api_version()
    build_id = library.correctionlib_plugin_build_id().decode("ascii")
    install = Path(correctionlib.__file__).parent
    native = install / "lib/libcorrectionlib.so"
    headers = (install / "include/correction.h", install / "include/correction_plugin.h")
    expected = ":".join(hashlib.sha256(header.read_bytes()).hexdigest() for header in headers)
    assert version == 4 and f":{expected}:" in build_id, build_id
    return {"version": version, "build_id": build_id, "compiled_binding": _core.__file__, "library": str(native)}


def selection_probes(nodes, path):
    fixtures = json.loads(path.read_text())
    counts = {}
    for name in ("eta", "vertex", "track_roles"):
        correction = nodes[f"muon_selection_{name}"]
        for probe in fixtures[name]:
            np.testing.assert_array_equal(correction.evaluate(*probe["arguments"]), probe["expected"])
        counts[name] = len(fixtures[name])
    selection = nodes["muon_selection_after_calibration"]
    def evaluate(node, row):
        return node.evaluate(*((float if item.type == "real" else int)(row[item.name]) for item in node.inputs))

    baseline = fixtures["after_calibration_defaults"]
    for probe in fixtures["after_calibration"]:
        row = baseline | probe["overrides"]
        composite = evaluate(selection, row)
        np.testing.assert_array_equal(composite, probe["expected"], err_msg=probe["id"])
        passed = 0xFFFFFFFF
        pt = evaluate(nodes["muon_selection_pt"], row) if row["eta_bits"] == passed else 0xFFFFFFFE
        track = evaluate(nodes["muon_selection_track"], row) if pt == passed else 0xFFFFFFF8
        medium, bad = evaluate(nodes["muon_selection_quality"], row) if track == passed else (0, 0)
        isolated = evaluate(nodes["muon_selection_isolation"], row) if medium == 1 else 0
        final = evaluate(nodes["muon_selection_final"], {"eta_bits": row["eta_bits"], "pt_bits": pt, "track_bits": track, "medium": medium, "isolated": isolated})
        np.testing.assert_array_equal(composite, [pt, track, medium, bad, isolated, final], err_msg=probe["id"])
    for probe in fixtures["exceptions"]:
        row = baseline | probe["overrides"]
        try:
            evaluate(selection, row)
        except (IndexError, RuntimeError):
            continue
        raise AssertionError(probe["id"])
    counts.update({key: len(fixtures[key]) for key in ("after_calibration", "exceptions")})
    counts["composition"] = len(fixtures["after_calibration"])
    return counts


def efficiency_probes(nodes, original_a, events):
    states, only_weight = systematic_labels(original_a / "hist-reference.root")
    tools = {"reco": "reco", "isol": "iso", "TTVA": "ttva"}
    with open_local(original_a / "reference.root") as handle:
        observed = handle["analysis"].arrays(entry_stop=events, how=dict, library="ak")
    values = {name: ak.to_numpy(ak.flatten(array)) for name, array in observed.items() if name.startswith(("mu_", "source_"))}
    runs = ak.to_numpy(ak.flatten(ak.broadcast_arrays(observed["randomRunNumber"], observed["mu_eta"])[0])).astype(np.int64)
    summary = {}
    for state in states:
        kin = "NOSYS" if state in only_weight else state
        prepared = (runs, values[f"mu_pt_{kin}"].astype(np.float64), values["mu_eta"].astype(np.float64), values["mu_phi"].astype(np.float64), values[f"mu_detail_charge_{kin}"].astype(np.float64), values["source_muonType"].astype(np.int64), values[f"mu_select_medium_{kin}"].astype(np.int64))
        evaluated = []
        summary[state] = {}
        for stage, node in tools.items():
            prefix = f"mu_{stage}_effSF_medium"
            own = state if f"{prefix}_{state}" in values else kin
            weight = values[f"{prefix}_{own}"]
            code = values[f"mu_detail_{stage}_sf_status_{own}"]
            result = nodes[f"muon_eff_{node}"].evalv(state, *prepared)
            difference = np.abs(result[:, 0] - weight)
            bad = (result[:, 1] != code) | (difference > 1e-7 + 2e-7 * np.abs(weight))
            item = {"compared": len(result), "codes_mismatched": int((result[:, 1] != code).sum()), "values_outside_limit": int(bad.sum()), "values_bitwise_equal": int((result[:, 0].astype(np.float32).view(np.uint32) == weight.view(np.uint32)).sum()), "max_abs": float(difference.max(initial=0)), "oov": int((code == 1).sum())}
            if bad.any():
                index = int(np.flatnonzero(bad)[0])
                item["first"] = {"row": index, "candidate": result[index].tolist(), "A": [float(weight[index]), int(code[index])], "inputs": [state, *[column[index].item() for column in prepared]]}
            summary[state][stage] = item
            evaluated.append(result[:, 0])
        product = nodes["muon_eff_product"].evalv(*evaluated)
        original = values[f"mu_effSF_medium_{state}"]
        diff = np.abs(product - original)
        summary[state]["product"] = {"compared": len(product), "values_outside_limit": int((diff > 1e-7 + 2e-7 * np.abs(original)).sum()), "max_abs": float(diff.max(initial=0))}
    return summary


def main(provider: Path, payload: Path, fixtures: Path, original_a: Path, output: Path, events: int = 250):
    abi = loaded_abi(provider)
    plugins.load_library(provider.resolve())
    nodes = _core.CorrectionSet.from_file(str(payload))
    selection = selection_probes(nodes, fixtures)
    efficiency = efficiency_probes(nodes, original_a, events)
    mappings = {line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines() if ".so" in line and line.split()[-1].startswith("/")}
    assert abi["library"] in mappings and str(provider.resolve()) in mappings
    assert not any(Path(name).name.startswith(("libROOT", "libCore.so", "libHist.so", "libMathCore.so")) for name in mappings)
    failures = sum(stat["values_outside_limit"] for state in efficiency.values() for stat in state.values())
    evidence = {"python": sys.executable, "abi": abi, "loaded_libraries": sorted(mappings), "selection_probes": selection, "efficiency": efficiency, "failures": failures}
    with output.open("x") as handle:
        json.dump(evidence, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(f"CorrectionLib ABI {abi['version']} ({abi['build_id']})")
    print("Selection boundary cases:", selection)
    print(f"Efficiency states checked from original release observations: {len(efficiency)}; failures: {failures}")
    if failures:
        for state, stages in efficiency.items():
            for stage, result in stages.items():
                if result["values_outside_limit"]:
                    print(state, stage, result)
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
