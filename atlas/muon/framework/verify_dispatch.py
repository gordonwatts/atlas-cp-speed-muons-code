#!/usr/bin/env python3
"""Require bound native symbols and genuine calls from an AnalysisBase smoke."""

import argparse
import json
from pathlib import Path
import re

from release_symbols import SYMBOLS, exported_symbols


ENTRIES = (
    "eta", "pt", "momentum_state", "momentum", "energy", "track", "quality",
    "isolation", "reco", "iso", "ttva", "product", "final",
)

FROZEN_COMPONENTS = (
    "SystematicsSvc", "SelectionNameSvc", "seq1_SystematicsPrinter",
    "seq1_OnlyWeightSystematicsPrinter", "seq1_MuonShallowCopyAlg_AnaMuons",
    "seq1_MuonEtaCutAlg_AnaMuons", "seq1_MuonCalibrationAndSmearingAlg_AnaMuons",
    "seq1_MuonPtCutAlg_AnaMuons", "seq1_EnergyDecorator_AnaMuons",
    "seq1_MuonTrackSelectionAlg_AnaMuons_medium", "seq1_MuonSelectionAlg_AnaMuons_medium",
    "seq1_MuonIsolationAlg_AnaMuons_medium", "seq1_MuonEfficiencyScaleFactorAlgReco_AnaMuons_medium",
    "seq1_MuonEfficiencyScaleFactorAlgIsol_AnaMuons_medium",
    "seq1_MuonEfficiencyScaleFactorAlgTTVA_AnaMuons_medium",
    "seq1_MuonCombinedEfficiencyScaleFactorAlg_AnaMuons_medium",
    "seq1_ObjectSelectionSummary_AnaMuons_medium_analysis",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("bridge", type=Path)
    parser.add_argument("frozen_config", type=Path)
    arguments = parser.parse_args()
    bridge = arguments.bridge.resolve(strict=True)
    frozen = json.loads(arguments.frozen_config.read_text(encoding="utf-8-sig"))
    observed = json.loads((arguments.run / "full_config.json").read_text(encoding="utf-8-sig"))
    mismatched = [component for component in FROZEN_COMPONENTS
                  if component not in frozen or frozen[component] != observed.get(component)]
    if mismatched:
        raise RuntimeError(f"Unexpected candidate CPRun configuration for {mismatched}")
    signatures = {signature: mangled for mangled, signature in exported_symbols(bridge).items()}
    tracked = {signatures[signature] for _, signature in SYMBOLS.values()}
    log = (arguments.run / "job.log").read_text(errors="replace")
    counts = {}
    for name, count in re.findall(r"^MUON_B_DISPATCH (\w+) (\d+)\s*$", log, flags=re.MULTILINE):
        counts[name] = counts.get(name, 0) + int(count)
    missing = [entry for entry in ENTRIES if counts.get(entry, 0) == 0]
    if missing:
        raise RuntimeError(f"No AnalysisBase interposed dispatch observed for {missing}")
    mapping = json.loads((arguments.run / "binding-map.json").read_text())
    actual = {record["symbol"]: record["owner"] for record in mapping["symbols"].values()}
    if tracked != set(actual) or any(owner != str(bridge) for owner in actual.values()):
        raise RuntimeError(f"Unexpected live symbol provenance: {actual}")
    libraries = [Path(name).name for name in mapping["mapped_libraries"]]
    assert "libatlas_muon.so" in libraries and "libcorrectionlib.so" in libraries
    assert mapping["worker_threads"] == 1 and mapping["cpu_affinity"] == [18]
    (arguments.run / "dispatch-counts.json").write_text(json.dumps(counts, indent=2) + "\n")
    for entry in ENTRIES:
        print(entry, counts[entry], sep="\t")


if __name__ == "__main__":
    main()
