#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
test "$#" -ge 1
for label in "$@"; do
    export TOOL_MODE=$label
    python - <<'PY'
import json
import os
from pathlib import Path
import ROOT

ROOT.xAOD.Init().ignore()
for library in ("libMuonMomentumCorrectionsLib.so", "libMuonMomentumCorrectionsDict.so", "libMuonEfficiencyCorrectionsLib.so", "libMuonEfficiencyCorrectionsDict.so"):
    assert ROOT.gSystem.Load(library) >= 0, library

def initialize(tool, settings):
    for key, value in settings.items():
        assert tool.setProperty(key, value).isSuccess(), (tool.name(), key, value)
    assert tool.initialize().isSuccess(), tool.name()
    return {
        "properties_overridden": settings,
        "affecting": [str(state.name()) for state in tool.affectingSystematics()],
        "recommended": [str(state.name()) for state in tool.recommendedSystematics()],
    }

mode = os.environ["TOOL_MODE"]
if mode == "released-default":
    scheme, eta_split, breakdown, uncorrelated = "Corr_Scale", False, False, False
elif mode == "decorrelated":
    scheme, eta_split, breakdown, uncorrelated = "Decorr_Scale", False, True, False
elif mode == "allsys-eta":
    scheme, eta_split, breakdown, uncorrelated = "AllSys", True, True, False
elif mode == "allsys-unsplit":
    scheme, eta_split, breakdown, uncorrelated = "AllSys", False, True, False
elif mode == "bin-uncorrelated":
    scheme, eta_split, breakdown, uncorrelated = "AllSys", True, True, True
else:
    raise ValueError(mode)

result = {}
momentum = ROOT.CP.MuonCalibTool("checkpointMomentum")
result["momentum"] = initialize(momentum, {"calibMode": 0, "IsRun3Geo": True, "systematicScheme": scheme, "doEtaSagittaSys": eta_split})
for name, wp in (("reco", "Medium"), ("isolation", "PflowTight_VarRadIso"), ("ttva", "TTVA")):
    tool = ROOT.CP.MuonEfficiencyScaleFactors("checkpointEfficiency_" + name)
    result[name] = initialize(tool, {"WorkingPoint": wp, "CalibrationRelease": "251211_Preliminary_r24run3", "BreakDownSystematics": breakdown, "UncorrelateSystematics": uncorrelated})
print(mode, {name: {kind: len(result[name][kind]) for kind in ("affecting", "recommended")} for name in result})
result_dir = Path(os.environ["RESULTS"])
(result_dir / f"initialized-tools-{mode}.json").write_text(json.dumps(result, indent=2) + "\n")
PY
done
