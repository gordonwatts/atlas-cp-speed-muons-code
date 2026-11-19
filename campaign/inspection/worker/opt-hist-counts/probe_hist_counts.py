"""Exercise the actual histogram and NPZ statements from two visible scripts."""

import ast
from enum import Enum
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import hist
import numpy as np


class Mode(Enum):
    full = "full"
    nominal = "nominal"
    stored = "stored"


def index_of_assignment(body, name):
    return next(i for i, statement in enumerate(body)
                if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == name)


def code(body, filename):
    return compile(ast.Module(body=body, type_ignores=[]), str(filename), "exec")


def script_fragments(path):
    module = ast.parse(path.read_text(encoding="utf-8"))
    body = next(node.body for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    fill_index = next(i for i, node in enumerate(body)
                      if isinstance(node, ast.FunctionDef) and node.name == "fill_pass")
    fill = body[fill_index].body
    read_index = next(i for i, node in enumerate(fill) if isinstance(node, ast.With))
    chunks = next(node.body for node in fill[read_index].body if isinstance(node, ast.For))
    raw = next(node.body for node in chunks if isinstance(node, ast.If)
               and isinstance(node.test, ast.Name) and node.test.id == "stored")
    assert isinstance(raw[-1], ast.Continue)
    states = next(node.body for node in chunks if isinstance(node, ast.For)
                  and isinstance(node.target, ast.Name) and node.target.id == "state")
    return {
        "axes": code(body[index_of_assignment(body, "states"):fill_index], path),
        "histograms": code(fill[:index_of_assignment(fill, "events") + 1], path),
        "stored": code(raw[:-1], path),
        "kinematics": code(states[:1], path),
        "fill": code(states[index_of_assignment(states, "selected"):], path),
        "after_chunks": code(fill[read_index + 1:index_of_assignment(fill, "wall_seconds")], path),
        "publish": code(body[index_of_assignment(body, "pass_summaries"):index_of_assignment(body, "summary")], path),
    }


def fixture_rows(kinematic, empty, nonfinite_pt):
    if empty:
        return np.empty((0, 4)), np.empty(0, dtype=np.int64)
    pt_mev = [-1000., 0., np.nextafter(5000., -np.inf), 5000., 499999., 500000., 599000., np.nan]
    gate = [1, 1, 1, 1, 1, 1, 1, 0]
    if kinematic != "NOSYS":
        pt_mev[0], pt_mev[1], gate[2] = 1000., 10000., 0
    if nonfinite_pt:
        gate[-1] = 1
    rows = np.zeros((len(pt_mev), 4))
    rows[:, 3] = pt_mev
    return rows, np.asarray(gate)


def run_probe(fragments, workflow, mode, output, *, repeat=2, chunks=(False, True, False),
              invalid_weight=False, nonfinite_pt=False, invalid_stored=False):
    scope = {
        "mode": mode, "Mode": Mode, "workflow": workflow, "hist": hist, "np": np,
        "repeat": repeat, "output": output, "hashlib": hashlib, "MUON_PREFIX": "Muon.",
        "flat": lambda events, dtype: np.asarray([pt for event in events for pt in event], dtype=dtype),
    }
    exec(fragments["axes"], scope)
    counts_per_pass = []

    def fill_pass():
        exec(fragments["histograms"], scope)
        for empty in chunks:
            if mode is Mode.stored:
                raw_values = [np.inf] if invalid_stored else [-1000., 5000., 500000.]
                scope["arrays"] = {"Muon.pt": [[]] if empty else [raw_values]}
                exec(fragments["stored"], scope)
                continue
            cached_kinematics = {}
            for index, state in enumerate(scope["states"]):
                scope["state"] = state
                exec(fragments["kinematics"], scope)
                kin_state = scope["kin_state"]
                if kin_state not in cached_kinematics:
                    cached_kinematics[kin_state] = fixture_rows(kin_state, empty, nonfinite_pt)
                scope["m"], scope["gate"] = cached_kinematics[kin_state]
                weights = np.full(scope["gate"].size, (-1)**index * (index + 1) / 8.)
                if invalid_weight and state in scope["weight_only"] and weights.size:
                    weights[0] = np.inf
                scope["weights"] = weights
                exec(fragments["fill"], scope)

        assert tuple(scope["unweighted"].axes[1]) == tuple(scope["states"])
        assert not any(axis.traits.growth for axis in scope["unweighted"].axes)
        assert scope["unweighted"].axes[0].traits.underflow and scope["unweighted"].axes[0].traits.overflow
        assert not scope["unweighted"].axes[1].traits.underflow and not scope["unweighted"].axes[1].traits.overflow
        exec(fragments["after_chunks"], scope)
        observed = scope["unweighted"].view(flow=True)
        assert observed.flags.writeable and observed.dtype == np.int64
        assert observed.shape == (102, len(scope["states"]))
        counts_per_pass.append(observed.copy())
        return scope["weighted"], scope["unweighted"], {}

    scope["fill_pass"] = fill_pass
    exec(fragments["publish"], scope)
    if repeat > 1:
        assert len({row["histogram_sha256"] for row in scope["pass_summaries"]}) == 1
        for counts in counts_per_pass[1:]:
            np.testing.assert_array_equal(counts, counts_per_pass[0])


def main(control, candidate, workflow_path):
    scripts = [script_fragments(Path(path)) for path in (control, candidate)]
    workflow = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
    frozen = workflow["states"]
    assert len(frozen["all_ordered"]) == len(set(frozen["all_ordered"])) == 97
    assert len(frozen["kinematic"]) == 28 and len(frozen["weight_only"]) == 68
    assert set(frozen["kinematic"]).isdisjoint(frozen["weight_only"])
    assert set(frozen["all_ordered"]) == {"NOSYS", *frozen["kinematic"], *frozen["weight_only"]}
    reordered = {"states": {**frozen, "all_ordered": frozen["all_ordered"][1:] + ["NOSYS"]}}
    with TemporaryDirectory(prefix="m18a-histogram-") as directory:
        target = Path(directory)
        accepted = {}
        for name, mode, states, chunks in (
            ("full", Mode.full, workflow, (False, True, False)),
            ("reordered", Mode.full, reordered, (True, False)),
            ("nominal", Mode.nominal, workflow, (False, True, False)),
            ("stored", Mode.stored, workflow, (False, True, False)),
            ("zero_chunks", Mode.full, workflow, ()),
        ):
            first, second = (target / f"{name}-{suffix}.npz" for suffix in ("control", "candidate"))
            for fragments, output in zip(scripts, (first, second), strict=True):
                run_probe(fragments, states, mode, output, chunks=chunks)
            assert first.read_bytes() == second.read_bytes(), name
            with np.load(second, allow_pickle=False) as data:
                required_states = tuple(states["states"]["all_ordered"]) if mode is Mode.full else ("NOSYS",)
                assert tuple(data["states"]) == required_states
                if chunks:
                    assert np.all(np.count_nonzero(data["count"][[0, -1], :], axis=1) > 0)
                else:
                    assert not data["count"].any()
                accepted[name] = {key: value.copy() for key, value in data.items()}
        for key in ("count", "sumw", "sumw2"):
            np.testing.assert_array_equal(accepted["nominal"][key][:, 0], accepted["full"][key][:, 0])
        assert not np.array_equal(accepted["full"]["count"][:, 0], accepted["full"]["count"][:, 1])
        assert not np.array_equal(accepted["full"]["sumw"][:, 0], accepted["full"]["sumw"][:, 3])

        for mode, name, options in (
            (Mode.full, "weight-only nonfinite weight", {"invalid_weight": True}),
            (Mode.full, "selected nonfinite pT", {"nonfinite_pt": True}),
            (Mode.stored, "nonfinite stored pT", {"invalid_stored": True}),
        ):
            errors = []
            for fragments in scripts:
                output = target / "failed.npz"
                try:
                    run_probe(fragments, workflow, mode, output, **options)
                except ValueError as error:
                    errors.append(str(error))
                assert not output.exists(), name
            assert len(errors) == 2 and errors[0] == errors[1] and "Non-finite" in errors[0], name
    print("Exact NPZ, axis order and flows, original invalid-input guards and two-pass publication agree")


if __name__ == "__main__":
    main(*sys.argv[1:])
