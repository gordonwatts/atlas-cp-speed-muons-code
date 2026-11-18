#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
build_dir=${1:?fresh output directory required}
mkdir "$build_dir"
release="$AtlasArea/InstallArea/$CMTCONFIG"
external="$AtlasExternalsArea/InstallArea/$CMTCONFIG"
if [[ ! -d "$external/include" ]]; then
    external=${release/AnalysisBase\//AnalysisBaseExternals\/}
fi
{
    g++ --version
    root-config --cflags --libs
    set -x
    g++ $(root-config --cflags) -O2 -shared -fPIC -DXAOD_STANDALONE -DXAOD_ANALYSIS \
        -I"$release/include" -I"$external/include" -I"$external/include/eigen3" \
        "$reference_dir/MuonEfficiencyStatusObserver.cxx" -o "$build_dir/libMuonEfficiencyStatusObserver.so" \
        -L"$release/lib" -Wl,-z,defs -lMuonEfficiencyCorrectionsLib -lSystematicsHandlesLib -lSelectionHelpersLib \
        -lPATInterfaces -lAnaAlgorithmLib -lAsgDataHandlesLib -lAsgTools -lAsgMessagingLib -lAsgServicesLib -lCxxUtils \
        -lAthContainers -lxAODMuon -lxAODEventInfo -lxAODRootAccess -lxAODRootAccessInterfaces $(root-config --libs)
    set +x
    sha256sum "$reference_dir/MuonEfficiencyStatusObserver.cxx" "$build_dir/libMuonEfficiencyStatusObserver.so"
    ldd "$build_dir/libMuonEfficiencyStatusObserver.so"
} 2>&1 | tee "$build_dir/build.log"
