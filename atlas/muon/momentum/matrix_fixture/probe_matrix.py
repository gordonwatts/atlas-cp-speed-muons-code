"""Compare the full momentum node against a fresh process with the control provider."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

app = typer.Typer()
Input = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]


def track(role, q_over_p, phi=0.1, diagonal=(1., 1., 1., 1., 1.), off=()):
    return {
        f"{role}_valid": 1, f"{role}_d0": 0.125, f"{role}_z0": -0.25,
        f"{role}_q_over_p": q_over_p, f"{role}_phi": phi, f"{role}_theta": 1.25,
        f"{role}_cov_diag_size": 5, f"{role}_cov_off_diag_size": len(off) or 6,
        **{f"{role}_cov_diag_{index}": value for index, value in enumerate(diagonal)},
        **{f"{role}_cov_off_diag_{index}": value for index, value in enumerate(off)},
    }


def edge_rows():
    defaults = {
        "called": 1, "run_number": 410000, "event_number_lo": 314159, "event_number_hi": 0,
        "is_simulation": 1, "muon_type": 0, "mu_pt": 49_500., "mu_eta": -1.25,
        "mu_phi": 0.125, "mu_charge": 1.,
        **track("cb", 1.9e-5), **track("id", 2e-5), **track("me", 2.2e-5),
    }
    variants = (
        ("regular", {}, 2),
        ("singular_id", {"id_cov_diag_4": 0.}, 0),
        ("singular_me", {"me_cov_diag_4": 0.}, 0),
        ("singular_summed_weights", {"me_cov_diag_0": -1.}, 0),
        ("rank_cut_only_at_summed_covariance", {
            **track("id", 2e-5, diagonal=(1.,) + (2.**40,) * 4),
            **track("me", 2.2e-5, diagonal=(-1. + 2.**-23,) + (2.**40,) * 4),
        }, 0),
        ("rank_below_cut", {"id_cov_diag_4": 2.**-60}, 0),
        ("near_singular", {"id_cov_diag_4": 2.**-20}, 2),
        ("near_wrap_negative_charge", {
            "mu_charge": -1., **track("id", -2e-5, 3.1415927, off=(0.1, -0.2, 0., 0., 0.25, 0.)),
            **track("me", -2.2e-5, 3.1415927, off=(0., 0.1, 0., 0., -0.2, 0., 0., 0., 0.25, 0.)),
        }, 2),
        ("near_wrap_positive_charge", {"id_phi": -3.1415927, "me_phi": -3.1415927}, 2),
        ("zero_charge", {"mu_charge": 0.}, 2),
        ("missing_me", {"me_valid": 0}, -1),
        ("missing_before_singular", {"me_valid": 0, "id_cov_diag_4": 0.}, -1),
        ("not_called", {"called": 0, "id_cov_diag_size": 0}, -1),
    )
    return [(name, defaults | fields, status) for name, fields, status in variants]


def as_bits(values):
    return np.asarray(values, dtype=np.float64).view(np.uint64)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path, data):
    with path.open("x", encoding="utf-8") as output:
        json.dump(data, output, indent=2)
        output.write("\n")


@app.command()
def capture(provider: Input, production: Input, trace: Input, contract: Input, output: Path):
    from correctionlib import _core, plugins

    provider = provider.resolve()
    plugins.load_library(provider)
    contract_data = json.loads(contract.read_text(encoding="utf-8"))
    expected_outputs = contract_data["outputs"]
    production_width = contract_data["production_output_count"]
    definition = _core.CorrectionSet.from_file(str(production))[contract_data["correction"]]
    diagnostic = _core.CorrectionSet.from_file(str(trace))[contract_data["correction"]]
    assert diagnostic.output_size == len(expected_outputs) == 40
    assert definition.output_size == production_width == 12
    trace_data = json.loads(trace.read_text(encoding="utf-8"))
    states = next(item for item in trace_data["corrections"] if item["name"] == contract_data["correction"])
    state_names = list(states["data"]["config"]["states"])
    assert len(state_names) == 29

    cases = edge_rows()
    fields = contract_data["inputs"]
    status_index = expected_outputs.index("sagitta_stat_comb_status")
    samples = {}
    for state in state_names:
        observations = []
        rows = [
            [state] + [values.get(field["name"], 0 if field["type"] == "int" else 0.) for field in fields[1:]]
            for _, values, _ in cases
        ]
        for row, (name, _, status) in zip(rows, cases, strict=True):
            values = diagnostic.evaluate(*row)
            if values[status_index] != status:
                raise AssertionError(f"{state}/{name}: statistical status {values[status_index]} != {status}")
            np.testing.assert_array_equal(as_bits(definition.evaluate(*row)), as_bits(values[:production_width]))
            observations.append(values)
        columns = [state] + [
            np.asarray([row[index] for row in rows], dtype={"int": np.int64, "real": np.float64}[field["type"]])
            for index, field in enumerate(fields[1:], start=1)
        ]
        scalar_bits = as_bits(observations)
        np.testing.assert_array_equal(as_bits(diagnostic.evalv(*columns)), scalar_bits)
        np.testing.assert_array_equal(as_bits(definition.evalv(*columns)), scalar_bits[:, :production_width])
        samples[state] = [[f"{value:016x}" for value in row] for row in scalar_bits]

    mappings = Path("/proc/self/maps").read_text(encoding="utf-8").splitlines()
    binaries = {
        Path(parts[5])
        for line in mappings
        if len(parts := line.split(maxsplit=5)) == 6 and parts[5].startswith("/")
        and Path(parts[5]).name.startswith(("libcorrectionlib.so", "libCore.so", "libMathCore.so"))
    }
    if not any(line.split(maxsplit=5)[-1] == str(provider) for line in mappings):
        raise RuntimeError("Provider mapping missing from the current probe process")
    save(output, {
        "loaded_binaries": {str(path): digest(path) for path in sorted(binaries | {provider})},
        "inputs": {key: digest(path) for key, path in (("production", production), ("trace", trace), ("contract", contract))},
        "state_order": state_names,
        "case_order": [name for name, _, _ in cases],
        "statuses": [status for _, _, status in cases],
        "output_order": expected_outputs,
        "trace_bits": samples,
    })


@app.command()
def compare(control: Input, candidate: Input, output: Path):
    old, new = (json.loads(path.read_text(encoding="utf-8")) for path in (control, candidate))
    for key in ("inputs", "state_order", "case_order", "statuses", "output_order"):
        if old[key] != new[key]:
            raise AssertionError(f"Fixture contract changed: {key}")
    differences = []
    for state in old["state_order"]:
        for row, case in enumerate(old["case_order"]):
            baseline = old["trace_bits"][state][row]
            trial = new["trace_bits"][state][row]
            for lane, first, second in zip(old["output_order"], baseline, trial, strict=True):
                if first != second:
                    differences.append({"state": state, "case": case, "lane": lane, "control": first, "candidate": second})
    save(output, {
        "states": len(old["state_order"]), "cases": len(old["case_order"]), "trace_width": len(old["output_order"]),
        "control_loaded_binaries": old["loaded_binaries"], "candidate_loaded_binaries": new["loaded_binaries"],
        "differences": differences,
    })
    if differences:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
