#!/usr/bin/env bash
set -eo pipefail

framework_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
framework_output=${1:?Provide an absolute, fresh framework build directory}
[[ "$framework_output" = /* ]]
mkdir "$framework_output"
release="${AtlasArea:?Source the campaign release environment}/InstallArea/${CMTCONFIG:?}"
external="${AtlasExternalsArea:-}/InstallArea/$CMTCONFIG"
if [[ ! -d "$external/include" ]]; then
    external=${release/AnalysisBase\//AnalysisBaseExternals\/}
fi
if [[ -n "${ATLAS_MUON_CORE_LIBRARY:-}" && -n "${ATLAS_MUON_CORE_INCLUDE:-}" ]]; then
    framework_core_lib=$ATLAS_MUON_CORE_LIBRARY
    framework_core_include=$ATLAS_MUON_CORE_INCLUDE
else
    framework_core_package=$(python -c 'import correctionlib; from pathlib import Path; print(Path(correctionlib.__file__).resolve().parent)')
    framework_core_lib=${ATLAS_MUON_CORE_LIBRARY:-"$framework_core_package/lib/libcorrectionlib.so"}
    framework_core_include=${ATLAS_MUON_CORE_INCLUDE:-"$framework_core_package/include"}
fi
[[ -f "$framework_core_lib" && -f "$framework_core_include/correction_plugin.h" ]]
[[ $(root-config --version) == 6.40.02 ]]
read -r -a root_cflags <<< "$(root-config --cflags)"
read -r -a root_libs <<< "$(root-config --libs)"
framework_library="$framework_output/libAtlasMuonFramework.so"
probe_flags=()
if [[ "${ATLAS_MUON_B_ALIAS_PROBE:-0}" == 1 ]]; then
    probe_flags+=(-DATLAS_MUON_B_ALIAS_PROBE)
fi
{
    g++ --version
    root-config --cflags --libs
    python "$framework_source/release_symbols.py" "$release/lib" "$framework_output/release_symbols.h"
    set -x
    g++ "${root_cflags[@]}" "${probe_flags[@]}" -O2 -g -shared -fPIC -DXAOD_STANDALONE -DXAOD_ANALYSIS \
        -DCOLUMNAR_MODE_NAME=ColumnarModeXAOD -DCOLUMNAR_MODE_SUFFIX= \
        -I"$framework_output" -I"$framework_core_include" -I"$release/include" \
        -I"$external/include" -I"$external/include/eigen3" \
        "$framework_source/framework_bindings.cxx" "$framework_source/framework_runtime.cxx" \
        -o "$framework_library" -L"$release/lib" -Wl,-z,defs -Wl,--no-as-needed \
        "$framework_core_lib" -Wl,-rpath,"$(dirname -- "$framework_core_lib")" \
        -lAsgAnalysisAlgorithmsLib -lMuonAnalysisAlgorithmsLib -lMuonMomentumCorrectionsLib \
        -lMuonEfficiencyCorrectionsLib -lColumnarCoreLib -lSystematicsHandlesLib -lSelectionHelpersLib \
        -lPATCoreLib -lPATInterfaces -lAnaAlgorithmLib -lAsgDataHandlesLib -lAsgTools \
        -lAsgMessagingLib -lAsgServicesLib -lCxxUtils -lAthContainers -lxAODBase \
        -lxAODMuon -lxAODEventInfo -lxAODTracking -lxAODRootAccess -lxAODRootAccessInterfaces \
        -ldl "${root_libs[@]}"
    set +x
    python "$framework_source/release_symbols.py" "$release/lib" "$framework_output/release_symbols.h" \
        --bridge "$framework_library"
    sha256sum "$framework_library" "$framework_core_lib" "$framework_source"/*.{cxx,h,py,sh}
    ldd "$framework_library"
} 2>&1 | tee "$framework_output/build.log"
