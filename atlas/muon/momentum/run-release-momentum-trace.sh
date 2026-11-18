#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
destination=${1:?fresh output directory required}
mkdir "$destination"
release="$AtlasArea/InstallArea/$CMTCONFIG"
external=${release/AnalysisBase\//AnalysisBaseExternals\/}
{
    set -x
    g++ $(root-config --cflags) -O2 -DXAOD_STANDALONE -DXAOD_ANALYSIS \
      -DCOLUMNAR_MODE_NAME=ColumnarModeXAOD -DCOLUMNAR_MODE_SUFFIX= \
      -I"$release/include" -I"$external/include" -I"$external/include/eigen3" \
      "$repo/atlas/muon/momentum/release_momentum_trace.cxx" -o "$destination/release_momentum_trace" \
      -L"$release/lib" -lMuonMomentumCorrectionsLib -lMuonSelectorToolsLib -lColumnarCoreLib \
      -lPATInterfaces -lAsgTools -lAsgDataHandlesLib -lAsgMessagingLib -lAsgServicesLib -lCxxUtils \
      -lAthContainers -lxAODMuon -lxAODEventInfo -lxAODTracking -lxAODRootAccess -lxAODRootAccessInterfaces \
      $(root-config --libs)
    "$destination/release_momentum_trace" "$INPUT" "$destination/trace.tsv"
    set +x
    wc -l "$destination/trace.tsv"
    sha256sum "$destination/trace.tsv" "$destination/release_momentum_trace"
} 2>&1 | tee "$destination/job.log"
