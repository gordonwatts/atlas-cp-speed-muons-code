#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
src=https://gitlab.cern.ch/atlas/athena/-/raw/6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c
paths=(
    PhysicsAnalysis/Algorithms/SelectionHelpers/Root/OutOfValidityHelper.cxx
    PhysicsAnalysis/Algorithms/AsgAnalysisAlgorithms/Root/AsgLeptonTrackSelectionAlg.cxx
    PhysicsAnalysis/Algorithms/AsgAnalysisAlgorithms/Root/AsgObjectScaleFactorAlg.cxx
    PhysicsAnalysis/Algorithms/SystematicsHandles/Root/SystematicsSvc.cxx
    PhysicsAnalysis/MuonID/MuonIDAnalysis/MuonMomentumCorrections/Root/CalibContainer.cxx
    PhysicsAnalysis/MuonID/MuonSelectorTools/Root/MuonSelectionTool.cxx
    PhysicsAnalysis/AnalysisCommon/IsolationSelection/Root/IsolationSelectionTool.cxx
)
for path in "${paths[@]}"; do
    curl --silent --show-error --fail --max-time 12 "$src/$path" -o "$RESULTS/source/${path##*/}" || true
done
sha256sum "$RESULTS"/source/*.cxx > "$RESULTS/athena-source-sha256.txt"
python - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import ROOT

results = Path(os.environ["RESULTS"])
tool_config = json.loads((results / "initialized-tools-allsys-eta.json").read_text())
smoke = json.loads((results / "smoke-observer-02" / "smoke-evidence.json").read_text())
for config in tool_config.values():
    assert set(config["recommended"]) == set(config["affecting"])
assert set(tool_config["momentum"]["recommended"]) == set(smoke["states"]["kinematic"])
assert {state for name in ("reco", "isolation", "ttva") for state in tool_config[name]["recommended"]} == set(smoke["states"]["weight_only"])
print("Direct tool recommendations match the original framework state labels exactly")

momentum = Path("MuonMomentumCorrections/Recs2025_12_11_Run2Run3")
efficiency = Path("MuonEfficiencyCorrections/251211_Preliminary_r24run3")
tracks = ("ID", "ME", "CB")
years = ("Data16", "Data17", "Data18", "Data22", "Data23", "Data24", "Data25", "Run4")
relative = [momentum / "ScaleAndSmear" / year / f"{track}.root" for year in years for track in tracks]
relative += [momentum / "ScaleAndSmear" / year / "MC_values_muons.root" for year in years]
relative += [momentum / "sagittaBias" / year / f"{track}_{mode}.root" for year in years for track in tracks for mode in ("data", "mc")]
relative += [efficiency / table for table in ("Reco_Medium_Z.root", "Reco_Medium_JPsi.root", "Reco_CaloTag_Z.root", "Reco_CaloTag_JPsi.root", "Reco_HighEta_Z.root", "Iso_PflowTight_VarRadIso_Z.root", "TTVA_Z.root")]
relative += [Path("MuonSelectorTools/PreRec2016_2016-04-13/muonSelection_tightWPHisto.root"), Path("MuonSelectorTools/180620_BMVmimicCutFunctions/BMVmimicCutFunctions.root")]

assert ROOT.gSystem.Load("libPathResolver.so") >= 0
manifest = []
for relative_name in relative:
    found = str(ROOT.PathResolver.FindCalibFile(str(relative_name)))
    assert found, relative_name
    path = Path(found)
    with path.open("rb") as handle:
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    manifest.append({"relative": str(relative_name), "path": str(path), "resolved": str(path.resolve()), "size": path.stat().st_size, "sha256": sha256})
(results / "resolved-calibration-data.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"PathResolver resolved and SHA-256 hashed {len(manifest)} pinned calibration dependencies")
PY
