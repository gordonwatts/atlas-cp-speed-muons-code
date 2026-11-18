#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
cd "$CAMPAIGN"
base=/cvmfs/atlas.cern.ch/repo/sw/software/25.2/AnalysisBase/25.2.117/InstallArea/x86_64-el9-gcc15-opt
find -L "$base/share" "$base/data" "$base/python" \( -name '*.yaml' -o -name '*.yml' \) -print > "$RESULTS/release-yaml-files.txt"
rg -ni 'pflowtight_varrad|pflowloose_varrad|systematicslist|systematicsfilter|addselectiontopreselection|class Output' "$base/python/AnalysisAlgorithmsConfig" "$base/python/AsgAnalysisAlgorithms" > "$RESULTS/config-references.txt" || true
rg -ni 'Corr_Scale|systematicScheme|deco.*sys|BreakDownSystematics|calibration.*release' "$base/include/MuonMomentumCorrections" "$base/include/MuonEfficiencyCorrections/MuonEfficiencyScaleFactors.h" > "$RESULTS/muon-header-references.txt"
rg -ni 'override|Custom|algorithmConfig' "$base/python/AnalysisAlgorithmsConfig/ConfigText.py" "$base/python/AnalysisAlgorithmsConfig/ConfigAccumulator.py" "$base/python/AnalysisAlgorithmsConfig/CPBaseRunner.py" > "$RESULTS/override-references.txt" || true
sha256sum "$base/python/MuonAnalysisAlgorithms/MuonAnalysisConfig.py" "$base/include/MuonMomentumCorrections/MuonCalibTool.h" "$base/include/MuonEfficiencyCorrections/MuonEfficiencyScaleFactors.h" "$base/include/MuonSelectorTools/MuonSelectionTool.h" > "$RESULTS/release-source-sha256.txt"
mkdir "$RESULTS/source"
src=https://gitlab.cern.ch/atlas/athena/-/raw/6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c
for name in MuonCalibTool MuonCalibIntScaleSmearTool MuonCalibIntSagittaTool MuonCalibIntHighpTSmearTool CalibInitializer; do
    curl --silent --show-error --fail --max-time 12 "$src/PhysicsAnalysis/MuonID/MuonIDAnalysis/MuonMomentumCorrections/Root/$name.cxx" -o "$RESULTS/source/$name.cxx" || true
done
for name in MuonEfficiencyScaleFactors EfficiencyScaleFactor EffiCollection; do
    curl --silent --show-error --fail --max-time 12 "$src/PhysicsAnalysis/MuonID/MuonIDAnalysis/MuonEfficiencyCorrections/Root/$name.cxx" -o "$RESULTS/source/$name.cxx" || true
done
for name in MuonCalibrationAndSmearingAlg MuonSelectionAlg MuonIsolationAlg MuonEfficiencyScaleFactorAlg ; do
    curl --silent --show-error --fail --max-time 12 "$src/PhysicsAnalysis/Algorithms/MuonAnalysisAlgorithms/Root/$name.cxx" -o "$RESULTS/source/$name.cxx" || true
done
