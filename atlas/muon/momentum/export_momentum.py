"""Export the pinned MuonCalibTool maps into an API-4 native correction."""

import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Annotated

import typer

ATHENA = "6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c"
CORE = "3dcfa20ec01fb40c4d5fec58383785519dec1c00"
RELEASE = "Recs2025_12_11_Run2Run3"
YEARS = ("Data16", "Data17", "Data18", "Data22", "Data23", "Data24", "Data25", "Run4")
TRACKS = ("CB", "ID", "ME")
SCALE_PARAMS = ("s0", "s1", "r0", "r1", "r2")
SCALE_VARIANTS = ("nom", "upErr", "downErr")
EXPECTED_MAPS = ("r1_ID_MC", "r2_ID_MC", "r2_ID_TAN2_MC", "r0_MS_MC", "r1_MS_MC", "r2_MS_MC")
# Each row gives the release's signed direction relative to the public up state.
# The adjacent action encodes the source table or sagitta branch.
VARIATIONS = {
    "MUON_CB": ("smear_cb", 1), "MUON_ID": ("smear_id", 1), "MUON_MS": ("smear_me", 1),
    "MUON_SCALE_CB": ("scale_cb_s1", -1), "MUON_SCALE_CB_ELOSS": ("scale_cb_s0", -1),
    "MUON_SCALE_ID": ("scale_id", -1), "MUON_SCALE_MS": ("scale_me_s1", -1),
    "MUON_SCALE_MS_ELOSS": ("scale_me_s0", -1),
    "MUON_SAGITTA_DATASTAT": ("datastat", 1), "MUON_SAGITTA_GLOBAL": ("global", -1),
    "MUON_SAGITTA_PTEXTRA": ("ptextra", -1), "MUON_SAGITTA_RESBIAS_NEGETA": ("negeta", -1),
    "MUON_SAGITTA_RESBIAS_POSETA": ("poseta", -1), "MUON_SAGITTA_RHO": ("rho", -1),
}


def binary64(value):
    """Encode the exact value returned by ROOT, including any nonfinite bins."""
    return struct.pack(">d", float(value)).hex()


def export_axis(axis):
    bins = axis.GetNbins()
    low, high = axis.GetXmin(), axis.GetXmax()
    edges = [axis.GetXbins().At(i) for i in range(axis.GetXbins().GetSize())]
    if not (bins > 0 and math.isfinite(low) and math.isfinite(high) and low < high):
        raise ValueError(f"Invalid ROOT axis bounds: {bins}, {low}, {high}")
    if edges and (
        len(edges) != bins + 1
        or edges[0] != low
        or edges[-1] != high
        or not all(math.isfinite(x) for x in edges)
        or not all(a < b for a, b in zip(edges, edges[1:]))
    ):
        raise ValueError(f"Inconsistent variable ROOT axis: {edges}")
    return [bins, binary64(low), binary64(high), [binary64(x) for x in edges]]


class RootMaps:
    def __init__(self, root, rows):
        self.root = root
        self.inventory = {item["relative"]: item for item in rows}
        if len(self.inventory) != len(rows):
            raise ValueError("Duplicate calibration dependencies in the inventory")
        self.files = {}

    def read(self, relative, keys):
        expected = self.inventory[relative]
        resolved = self.root.PathResolver.FindCalibFile(relative)
        if not resolved:
            raise RuntimeError(f"PathResolver could not resolve {relative}")
        path = Path(str(resolved)).resolve(strict=True)
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != expected["sha256"] or path.stat().st_size != expected["size"]:
            raise RuntimeError(f"Calibration differs from the campaign inventory: {relative} ({path})")
        if str(path) != str(Path(expected["resolved"]).resolve(strict=True)):
            raise RuntimeError(f"Calibration resolver selected an unexpected file for {relative}: {path}")

        result, provenance = [], {}
        source = self.root.TFile.Open(str(path), "READ")
        if not source or source.IsZombie():
            raise RuntimeError(f"ROOT could not open {path}")
        try:
            for key, optional in keys:
                obj = source.Get(key)
                # GetObject(TH2*&) treats an object with the wrong class as absent.
                if not obj or not obj.InheritsFrom("TH2"):
                    if not optional:
                        raise RuntimeError(f"Missing required TH2 {key} in {path}")
                    result.append(None)
                    provenance[key] = None
                    continue
                nx, ny = obj.GetNbinsX(), obj.GetNbinsY()
                if obj.InheritsFrom("TH2Poly") or obj.GetNcells() != (nx + 2) * (ny + 2):
                    raise RuntimeError(f"Unsupported TH2 storage for {key} in {path}")
                # Include both flow bins on each axis. ROOT's epsilon clamp can
                # round back to the endpoint and then select the overflow bin.
                cells = [binary64(obj.GetBinContent(i)) for i in range((nx + 2) * (ny + 2))]
                result.append([export_axis(obj.GetXaxis()), export_axis(obj.GetYaxis()), cells])
                provenance[key] = {"class": obj.ClassName(), "nx": nx, "ny": ny, "flow": True}
        finally:
            source.Close()
        self.files[relative] = {
            "resolved": str(path), "sha256": digest, "size": expected["size"], "objects": provenance
        }
        return result


def export_year(maps, year):
    prefix = f"MuonMomentumCorrections/{RELEASE}"
    scale_dir = f"{prefix}/ScaleAndSmear/{year}"
    sagitta_dir = f"{prefix}/sagittaBias/{year}"
    scale_keys = [(f"{param}_{variant}", False) for param in SCALE_PARAMS for variant in SCALE_VARIANTS]
    scale = [maps.read(f"{scale_dir}/{track}.root", scale_keys) for track in TRACKS]
    expected = maps.read(f"{scale_dir}/MC_values_muons.root", [(key, False) for key in EXPECTED_MAPS])
    sagitta = []
    for track in TRACKS:
        data = maps.read(
            f"{sagitta_dir}/{track}_data.root",
            [(f"p{track}_{name}", name == "ptExtra") for name in ("0", "statError", "1", "ptExtra")],
        )
        mc = maps.read(f"{sagitta_dir}/{track}_mc.root", [(f"p{track}_0", False)])
        sagitta.append(data + mc)
    return {"name": year, "scale": scale, "expected": expected, "sagitta": sagitta}


def systematic_config(discovery):
    properties = discovery["properties_overridden"]
    if properties != {"calibMode": 0, "IsRun3Geo": True, "systematicScheme": "AllSys", "doEtaSagittaSys": True}:
        raise ValueError(f"Discovery does not describe the frozen momentum setup: {properties}")
    states = {"NOSYS": ["nominal", 0]}
    for base, (action, sign) in VARIATIONS.items():
        states[f"{base}__1down"] = [action, -sign]
        states[f"{base}__1up"] = [action, sign]
    reference = discovery["recommended"]
    if len(reference) != 28 or set(reference) != states.keys() - {"NOSYS"} or reference != discovery["affecting"]:
        raise ValueError("Exported state list differs from initialized momentum tools")
    return {name: states[name] for name in ["NOSYS", *reference]}


def write_json(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(data, target, separators=(",", ":"), allow_nan=False)
        target.write("\n")


def main(
    inventory: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    discovery: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    payload: Annotated[Path, typer.Option()],
    provenance: Annotated[Path, typer.Option()],
    diagnostics: Annotated[bool, typer.Option(help="Include validation-only momentum stages.")] = False,
):
    """Run once in the initialized release environment with PyROOT and PathResolver."""
    import ROOT

    if ROOT.gROOT.GetVersion().replace("/", ".") != "6.40.02":
        raise RuntimeError(f"Expected release ROOT 6.40.02, found {ROOT.gROOT.GetVersion()}")
    if ROOT.gSystem.Load("libPathResolverDict") < 0:
        raise RuntimeError("Could not load the release PathResolver library")
    if payload.resolve() == provenance.resolve() or payload.exists() or provenance.exists():
        raise FileExistsError("Choose distinct fresh payload and provenance paths")
    contract = json.loads(Path(__file__).with_name("contract.json").read_text(encoding="utf-8"))
    inventory_bytes = inventory.read_bytes()
    discovery_bytes = discovery.read_bytes()
    states = systematic_config(json.loads(discovery_bytes)["momentum"])
    maps = RootMaps(ROOT, json.loads(inventory_bytes))
    years = [export_year(maps, year) for year in YEARS]
    fixed_rho = binary64(1.0)
    extra_rebias_float = struct.unpack(">f", struct.pack(">f", 0.00002))[0]
    config = {
        "release": RELEASE,
        "athena": ATHENA,
        "rng": "ROOT-6.40.02-TRandom3-Gaus",
        "fixed_rho": fixed_rho,
        "extra_rebias": binary64(extra_rebias_float),
        "years": years,
        "states": states,
    }
    outputs = contract["outputs"] if diagnostics else contract["outputs"][:contract["production_output_count"]]
    shape = [len(outputs)]
    definition = {
        "schema_version": 3,
        "description": "ATLAS MuonCalibTool correctData_CB AllSys with eta-split sagitta",
        "corrections": [{
            "name": contract["correction"],
            "description": "Calibrated muon momentum, storage masks, and stage observations",
            "version": contract["version"],
            "inputs": contract["inputs"],
            "output": {"name": "momentum", "type": "real", "shape": shape},
            "data": {
                "nodetype": "extension", "type": contract["node"], "version": contract["version"],
                "output_shape": shape, "config": config,
            },
        }],
    }
    write_json(payload, definition)
    write_json(provenance, {
        "athena": ATHENA,
        "correctionlib_core": CORE,
        "root": str(ROOT.gROOT.GetVersion()),
        "release": RELEASE,
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "discovery_sha256": hashlib.sha256(discovery_bytes).hexdigest(),
        "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "inputs": contract["inputs"],
        "outputs": outputs,
        "diagnostics": diagnostics,
        "states": states,
        "files": maps.files,
    })


if __name__ == "__main__":
    typer.run(main)
