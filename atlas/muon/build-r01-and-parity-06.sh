#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh ROOT-free build path required}
sdk=${2:?paired SDK path required}
input=${3:?accepted parity fixture path required}
src="$repo/atlas/muon"
sdk_dir="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
externals="${AtlasArea/AnalysisBase\//AnalysisBaseExternals\/}/InstallArea/$CMTCONFIG"
eigen=$(dirname "$(find "$externals" -iname Eigen3Config.cmake -print -quit)")
root=$(dirname "$(find "$externals" -iname ROOTConfig.cmake -print -quit)")
mkdir "$out"
{
    cmake -S "$src" -B "$out/provider-build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER="$(command -v g++)" \
         -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -Dcorrectionlib_DIR="$sdk_dir" -DEigen3_DIR="$eigen" \
         -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include" -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
         '-DCMAKE_INSTALL_RPATH=$ORIGIN/../../correctionlib/lib'
    cmake --build "$out/provider-build" --parallel 1 --target atlas_muon
    provider="$out/provider-build/libatlas_muon.so"
    sha256sum "$provider" "$src/momentum/root_compat.h"
    readelf -d "$provider" > "$out/provider-dynamic.txt"
    if rg -qi 'NEEDED.*lib(ROOT|Core\.so|Hist\.so|RIO\.so|MathCore\.so|Matrix\.so|Cling|Athena)' "$out/provider-dynamic.txt"; then
        echo "Unexpected framework or ROOT loader dependency" >&2
        exit 1
    fi
    cmake -S "$src/momentum/root_free_probe" -B "$out/parity-build" -DCMAKE_BUILD_TYPE=Release \
         -DCMAKE_CXX_COMPILER="$(command -v g++)" -DATLAS_R01_BUILD_ROOT_ORACLE=ON -DROOT_DIR="$root" \
         -DATLAS_R01_HEADER_DIR="$src/momentum"
    cmake --build "$out/parity-build" --parallel 1
    "$out/parity-build/r01_release_root" "$input/seeds.txt" "$input/axes.txt" > "$out/root.tsv"
    "$out/parity-build/r01_standalone" "$input/seeds.txt" "$input/axes.txt" > "$out/standalone.tsv"
    if ! cmp "$out/root.tsv" "$out/standalone.tsv"; then
        diff -u "$out/root.tsv" "$out/standalone.tsv" | head -50 || true
        exit 1
    fi
    echo "Release ROOT and standalone primitive output match byte for byte"
    wc -l "$out/"*.tsv
    sha256sum "$provider" "$input/"*.txt "$out/"*.tsv > "$out/artifact-sha256.txt"
    ldd "$out/parity-build/r01_release_root" > "$out/oracle-closure.txt"
    ldd "$out/parity-build/r01_standalone" > "$out/standalone-closure.txt"
} 2>&1 | tee "$out/job.log"
