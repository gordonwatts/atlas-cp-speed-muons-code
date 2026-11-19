"""Compare compact efficiency transport with the original dense CorrectionLib calls."""

from collections import Counter
import json
from pathlib import Path
from typing import Annotated

import awkward as ak
import numpy as np
import typer

from raw_input import EfficiencyRows, load_workflow, open_source

app = typer.Typer(no_args_is_help=True)
Input = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Output = Annotated[Path, typer.Argument(dir_okay=False)]
TOOLS = {"RECO": "muon_eff_reco", "ISO": "muon_eff_iso", "TTVA": "muon_eff_ttva"}
OBSERVER_TOOLS = ("reco", "isol", "TTVA")


def write(output, evidence):
    with output.open("x", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2)
        handle.write("\n")


def assert_exact(dense, compact, label):
    assert dense.dtype == compact.dtype and dense.shape == compact.shape, label
    assert dense.tobytes() == compact.tobytes(), label


def lanes_for(gates):
    gate = np.asarray(gates, dtype=np.int64)
    count = gate.size
    # True rows include a central accepted value and one far outside the
    # efficiency maps. Skipped rows deliberately have a run value that would
    # be rejected by the selected branch, and distinct coordinates.
    random_run = np.where(gate == 0, 2**32 + np.arange(count), 436332).astype(np.int64)
    pt = np.array([45000., 56000., 72000., 10e6, 62000., 110000.])[:count]
    eta = np.array([0.8, -0.35, 2.6, 12., 0.3, -0.1])[:count]
    phi = np.array([0.1, 0.6, 2.3, -0.2, -2.1, 0.75])[:count]
    charge = np.array([1., -1., -1., 1., 1., -1.])[:count]
    muon_type = np.zeros(count, dtype=np.int64)
    return random_run, pt, eta, phi, charge, muon_type, gate


def outcome(call):
    try:
        return None, call()
    except (ValueError, RuntimeError) as exc:
        return (type(exc).__name__, str(exc)), None


@app.command()
def edges(provider: Input, payload: Input, output: Output):
    """Use the same native payload to check compact rows, states, and edge outputs."""
    from correctionlib import _core, plugins

    plugins.load_library(provider.resolve())
    corrections = _core.CorrectionSet.from_file(str(payload))
    states = load_workflow()["states"]["weight_only"]
    active = {name: next(state for state in states if state.split("_", 3)[2] == name) for name in TOOLS}
    product = corrections["muon_eff_product"]
    evidence = {}
    for label, gates in (
        ("mixed", [0, 1, 0, 1, 1, 0]), ("all_skipped", [0, 0, 0, 0]),
        ("all_selected", [1, 1, 1, 1]), ("empty", []),
    ):
        full = lanes_for(gates)
        rows = EfficiencyRows(*full)
        selected = np.flatnonzero(full[-1] == 1)
        zeros = np.flatnonzero(full[-1] == 0)
        indices = np.append(selected, zeros[0]) if zeros.size else selected
        for source, compact in zip(full, rows.lanes, strict=True):
            assert_exact(source[indices], compact, label)
        assert_exact(full[1][selected], rows.selected_pt, label)
        assert rows.gate.size == selected.size + int(bool(zeros.size))

        dense = {}
        packed = {}
        oov = 0
        for name, key in TOOLS.items():
            node = corrections[key]
            for state in ("NOSYS", active[name]):
                reference = node.evalv(state, *full)
                result = node.evalv(state, *rows.lanes)
                assert reference.shape == (len(gates), 2)
                assert_exact(reference[indices], result, (label, name, state))
                assert_exact(reference, rows.expand(result), (label, name, state))
                assert np.all(reference[zeros, 1] == -1)
                assert np.all(np.isin(reference[selected, 1], [1, 2]))
                oov += np.count_nonzero(reference[selected, 1] == 1)
                dense[name, state] = reference[:, 0]
                packed[name, state] = result[:, 0]
        if label == "mixed":
            assert oov > 0, "Selected out-of-validity result missing from the fixture"

        for varied in (None, *TOOLS):
            combo = tuple((name, active[name] if name == varied else "NOSYS") for name in TOOLS)
            old = product.evalv(*(dense[key] for key in combo))
            new = product.evalv(*(packed[key] for key in combo))
            assert_exact(old, rows.expand(new), (label, varied))
            assert_exact(old[selected], rows.selected_values(new), (label, varied))
        evidence[label] = {"dense_rows": len(gates), "compact_rows": len(indices), "selected_oov": int(oov)}

    for label, gates, state in (
        ("unknown_state_all_skipped", [0, 0, 0], "INVALID_EFF_STATE"),
        ("unknown_state_mixed", [0, 1, 0], "INVALID_EFF_STATE"),
        ("unknown_state_empty", [], "INVALID_EFF_STATE"),
        ("invalid_gate", [0, 2, 1, -1], "NOSYS"),
        ("unknown_state_precedes_invalid_gate", [2, 0, 1], "INVALID_EFF_STATE"),
    ):
        full = lanes_for(gates)
        rows = EfficiencyRows(*full)
        if any(gate not in (0, 1) for gate in gates):
            assert all(lane is original for lane, original in zip(rows.lanes, full, strict=True))
        errors = {}
        for name, key in TOOLS.items():
            node = corrections[key]
            original_error, original_result = outcome(lambda: node.evalv(state, *full))
            packed_error, packed_result = outcome(lambda: node.evalv(state, *rows.lanes))
            assert original_error == packed_error, (label, name, original_error, packed_error)
            if gates:
                assert original_error is not None, (label, name)
            if label == "invalid_gate":
                assert "Efficiency gate must be 0 or 1" in original_error[1]
            if original_error is None:
                assert_exact(original_result, packed_result, (label, name))
            errors[name] = original_error
        evidence[label] = errors
    write(output, evidence)


@app.command()
def observers(control: Input, candidate: Input, output: Output):
    """Audit every original per-tool and combined observer row bitwise."""
    checked = Counter()
    with open_source(control) as a, open_source(candidate) as b:
        meta = json.loads(str(a["columnar_metadata"]))
        assert json.loads(str(b["columnar_metadata"])) == meta
        assert meta["entry_start"] == 0 and meta["entry_stop"] == 20000
        states = meta["states"]
        weights = meta["weight_only"]
        assert len(states) == 97 and len(weights) == 68
        left, right = a["analysis"], b["analysis"]
        assert left.num_entries == right.num_entries == 20000
        assert set(left.keys()) == set(right.keys())

        event_fields = ["entry", "eventNumber", "runNumber", "mcChannelNumber", "randomRunNumber"]
        fields = {"source_index": "objects"}
        for state in states:
            fields[f"mu_effSF_medium_{state}"] = "combined"
            if state not in weights:
                fields[f"mu_select_medium_{state}"] = "selection"
            for tool in OBSERVER_TOOLS:
                owner = {"reco": "RECO", "isol": "ISO", "TTVA": "TTVA"}[tool]
                if state not in weights or state.split("_", 3)[2] == owner:
                    fields[f"mu_{tool}_effSF_medium_{state}"] = "tool_sf"
                    fields[f"mu_detail_{tool}_sf_status_{state}"] = "tool_status"
                    fields[f"mu_detail_{tool}_sf_called_{state}"] = "tool_called"
        columns = [*event_fields, *fields]
        for lo in range(0, left.num_entries, 1000):
            hi = min(lo + 1000, left.num_entries)
            before = left.arrays(columns, entry_start=lo, entry_stop=hi, how=dict, library="ak")
            after = right.arrays(columns, entry_start=lo, entry_stop=hi, how=dict, library="ak")
            for name in event_fields:
                assert_exact(ak.to_numpy(before[name]), ak.to_numpy(after[name]), (name, lo))
            for name, role in fields.items():
                assert_exact(ak.to_numpy(ak.num(before[name])), ak.to_numpy(ak.num(after[name])), (name, lo))
                original_values = ak.to_numpy(ak.flatten(before[name]))
                candidate_values = ak.to_numpy(ak.flatten(after[name]))
                assert_exact(original_values, candidate_values, (name, lo))
                checked[role] += len(original_values)
                if role == "tool_called":
                    checked["true_tool_calls"] += np.count_nonzero(original_values)
    assert checked["objects"] == 14997
    assert checked["tool_sf"] == checked["tool_status"] == checked["tool_called"] == 2324535
    assert checked["true_tool_calls"] == 850902
    assert checked["combined"] == 1454709
    write(output, {"exact_values_compared": {key: int(value) for key, value in checked.items()}, "states": states})


if __name__ == "__main__":
    app()
