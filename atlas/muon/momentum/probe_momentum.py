"""Smoke-test the two correction output shapes and original RNG boundary."""

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated

import numpy as np
import typer


def record_loaded_binaries(root, provider, destination):
    fixed_names = {"libcorrectionlib.so", "libCore.so", "libHist.so", "libMathCore.so"}
    paths = set()
    for mapping in Path("/proc/self/maps").read_text(encoding="utf-8").splitlines():
        parts = mapping.split(maxsplit=5)
        if len(parts) != 6 or not parts[5].startswith("/"):
            continue
        path = Path(parts[5])
        if (
            path == provider
            or any(path.name == name or path.name.startswith(f"{name}.") for name in fixed_names)
            or (path.name.startswith("_core.") and path.parent.name == "correctionlib")
        ):
            paths.add(path)
    if provider not in paths or not any(path.name.startswith("libcorrectionlib.so") for path in paths):
        raise RuntimeError("The probe could not identify the live provider and SDK binaries")
    digests = {}
    for path in sorted(paths):
        with path.open("rb") as binary:
            digests[str(path)] = hashlib.file_digest(binary, "sha256").hexdigest()
    with destination.open("x", encoding="utf-8") as target:
        json.dump({"root_version": str(root.gROOT.GetVersion()), "loaded_binaries_sha256": digests}, target, indent=2)
        target.write("\n")


def main(
    provider: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    payload: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    trace_payload: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    runtime_provenance: Annotated[Path, typer.Option()],
):
    import ROOT
    from correctionlib import _core, plugins

    plugins.load_library(provider.resolve())
    contract = json.loads(Path(__file__).with_name("contract.json").read_text(encoding="utf-8"))
    production_count = contract["production_output_count"]
    definition = _core.CorrectionSet.from_file(str(payload))[contract["correction"]]
    diagnostic = _core.CorrectionSet.from_file(str(trace_payload))[contract["correction"]]
    assert definition.output_size == production_count
    assert diagnostic.output_size == len(contract["outputs"])
    fields = {name: i for i, name in enumerate(contract["outputs"])}

    defaults = {
        "systematic": "NOSYS", "called": 1, "run_number": 410000, "event_number_lo": 314159,
        "is_simulation": 1, "muon_type": 0, "mu_pt": 33123.1, "mu_eta": -1.25,
        "mu_phi": 0.125, "mu_charge": 1.0,
    }

    def values(**overrides):
        row = defaults | overrides
        return [row.get(item["name"], 0.0 if item["type"] == "real" else 0) for item in contract["inputs"]]

    def output(row, name):
        return row[fields[name]]

    missing = values()
    skipped = values(called=0, cb_valid=1)
    forward = values(muon_type=4)
    data_noncombined = values(is_simulation=0, muon_type=3, run_number=440613)
    id_diagonal = {f"id_cov_diag_{i}": 1.0 for i in range(5)}
    id_track = values(
        id_valid=1, id_q_over_p=0.0, id_phi=0.0, id_theta=float(np.float32(np.pi / 2)),
        id_cov_diag_size=5, id_cov_off_diag_size=6, **id_diagonal,
    )
    rows = [missing, skipped, forward, data_noncombined, id_track]
    checked = [diagnostic.evaluate(*row) for row in rows]

    nominal, bypassed, silicon, data, id_only = checked
    assert output(nominal, "year_index") == 3
    assert output(nominal, "status") == 2 and output(nominal, "write_mask") == 63
    assert output(nominal, "pt") == 0.0
    assert output(nominal, "id_pt") == 0.0 and output(nominal, "me_pt") == 0.0
    assert output(bypassed, "status") == -1 and output(bypassed, "write_mask") == 0
    assert output(bypassed, "pt") == np.float32(defaults["mu_pt"])
    assert math.isnan(output(bypassed, "id_pt"))
    assert output(silicon, "sagitta_called") == 0
    assert output(data, "scale_smear_called") == 0 and output(data, "write_mask") == 60
    assert output(data, "pt") == np.float32(defaults["mu_pt"])
    assert output(id_only, "raw_id_gev") == 10000.0
    assert output(id_only, "pt") == output(id_only, "id_pt")
    assert output(id_only, "sagitta_stat_comb_status") == -1

    stable_theta = float(np.float32(np.pi / 2))
    uncharged = diagnostic.evaluate(*values(
        mu_charge=0.0,
        id_valid=1, id_q_over_p=2e-5, id_theta=stable_theta,
        id_cov_diag_size=5, id_cov_off_diag_size=6,
        me_valid=1, me_q_over_p=2e-5, me_theta=stable_theta,
        me_cov_diag_size=5, me_cov_off_diag_size=10,
        **id_diagonal, **{f"me_cov_diag_{i}": 1.0 for i in range(5)},
    ))
    assert output(uncharged, "sagitta_stat_comb_status") == 2
    assert math.isnan(output(uncharged, "pt"))

    eta, phi = (float(np.float32(defaults[name])) for name in ("mu_eta", "mu_phi"))
    seed = int(1 + abs(phi) * 1e6 + abs(eta) * 1e3 + defaults["event_number_lo"])
    assert output(nominal, "root_seed") == seed
    random = ROOT.TRandom3()
    random.SetSeed(seed)
    for field in ("g0", "g1", "g2", "g3", "g4", "g_high_pt"):
        assert output(nominal, field) == random.Gaus(0, 1)

    malformed = values(cb_valid=1, cb_cov_diag_size=3, cb_cov_off_diag_size=6)
    try:
        diagnostic.evaluate(*malformed)
    except RuntimeError as exc:
        assert "DefiningParametersCovAccessor" in str(exc)
    else:
        raise AssertionError("Malformed covariance was accepted")

    varied = json.loads(trace_payload.read_text(encoding="utf-8"))["corrections"][0]["data"]["config"]["states"]
    assert len(varied) == 29
    for state in varied:
        row = id_track.copy()
        row[0] = state
        candidate = diagnostic.evaluate(*row)
        assert output(candidate, "status") == 2
        assert output(candidate, "root_seed") == seed
        np.testing.assert_array_equal(candidate[:production_count], definition.evaluate(*row))

    columns = [rows[0][0]]
    for i, item in enumerate(contract["inputs"][1:], start=1):
        dtype = np.int64 if item["type"] == "int" else np.float64
        columns.append(np.asarray([row[i] for row in rows], dtype=dtype))
    np.testing.assert_array_equal(diagnostic.evalv(*columns), checked)
    np.testing.assert_array_equal(definition.evalv(*columns), np.asarray(checked)[:, :production_count])
    assert diagnostic.evalv(columns[0], *(column[:0] for column in columns[1:])).shape == (0, len(fields))
    record_loaded_binaries(ROOT, provider.resolve(), runtime_provenance)
    print("29 states, original Gaussian draws, skip/write masks, track fallback and scalar/batch shapes passed")


if __name__ == "__main__":
    typer.run(main)
