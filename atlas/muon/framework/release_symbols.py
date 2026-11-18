#!/usr/bin/env python3
"""Resolve the pinned, exported, non-thunk ELF definitions used by the interposer."""

import argparse
from pathlib import Path
import subprocess


ASG = "libAsgAnalysisAlgorithmsLib.so"
MUON = "libMuonAnalysisAlgorithmsLib.so"
MOMENTUM = "libMuonMomentumCorrectionsLib.so"
EFFICIENCY = "libMuonEfficiencyCorrectionsLib.so"
SYMBOLS = {
    "pt_eta_accept": (ASG, "CP::AsgPtEtaSelectionTool::accept(xAOD::IParticle const*) const"),
    "momentum_apply_state": (MOMENTUM, "CP::MuonCalibTool::applySystematicVariation(CP::SystematicSet const&)"),
    "momentum_apply": (MOMENTUM, "CP::MuonCalibTool::applyCorrection(xAOD::Muon_v1&) const"),
    "efficiency_get": (
        EFFICIENCY,
        "CP::MuonEfficiencyScaleFactors::getEfficiencyScaleFactor(xAOD::Muon_v1 const&, float&, xAOD::EventInfo_v1 const*) const",
    ),
    "energy_execute": (ASG, "CP::AsgEnergyDecoratorAlg::execute(EventContext const&)"),
    "track_execute": (ASG, "CP::AsgLeptonTrackSelectionAlg::execute(EventContext const&) const"),
    "quality_execute": (MUON, "CP::MuonSelectionAlgV2::execute(EventContext const&)"),
    "isolation_execute": (MUON, "CP::MuonIsolationAlg::execute(EventContext const&)"),
    "product_execute": (ASG, "CP::AsgObjectScaleFactorAlg::execute(EventContext const&)"),
    "final_execute": (ASG, "CP::AsgSelectionAlg::execute(EventContext const&)"),
}


def exported_symbols(path):
    output = subprocess.run(
        ["nm", "-D", "--defined-only", "--format=posix", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    names = [line.split()[0] for line in output.splitlines() if line.strip()]
    demangled = subprocess.run(
        ["c++filt"], input="\n".join(names) + "\n",
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    return dict(zip(names, demangled, strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_lib", type=Path)
    parser.add_argument("header", type=Path)
    parser.add_argument("--bridge", type=Path, help="Require the built bridge to export the same members")
    options = parser.parse_args()
    library_symbols = {
        library: exported_symbols(options.release_lib / library)
        for library in sorted({library for library, _ in SYMBOLS.values()})
    }
    bridge = exported_symbols(options.bridge) if options.bridge else None
    records = []
    for label, (library, signature) in SYMBOLS.items():
        matches = [name for name, demangled in library_symbols[library].items() if demangled == signature]
        if len(matches) != 1:
            raise ValueError(f"{library} requires one exported symbol for {signature}. Found {matches}")
        (symbol,) = matches
        if bridge is not None and bridge.get(symbol) != signature:
            raise ValueError(f"Built bridge does not export {signature}")
        records.append(f'inline constexpr char {label}[] = "{symbol}";')
        print(library, symbol, signature, sep="\t")
    header = "\n".join((
        "#pragma once",
        "namespace muon_framework::release_symbols {",
        *records, "}", "",
    ))
    options.header.write_text(header, encoding="utf-8")


if __name__ == "__main__":
    main()
