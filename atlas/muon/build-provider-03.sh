#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
source_dir=${3:-$repo/atlas/muon}
output=${1:?fresh checkpoint provider path required}
sdk_venv=${2:?pinned SDK venv required}
mkdir "$output"
sdk_python="$sdk_venv/bin/python"
sdk_dir=$(env -u PYTHONPATH -u PYTHONHOME "$sdk_python" -c 'from pathlib import Path; import correctionlib; print(Path(correctionlib.__file__).parent / "cmake")')
externals="${AtlasArea/AnalysisBase\//AnalysisBaseExternals\/}/InstallArea/$CMTCONFIG"
eigen_dir=$(dirname "$(find "$externals" -iname Eigen3Config.cmake -print -quit)")
root_dir=$(dirname "$(find "$externals" -iname ROOTConfig.cmake -print -quit)")
export CMAKE_BUILD_PARALLEL_LEVEL=1
export CC=gcc CXX=g++
{
    printf 'SDK=%s\nROOT=%s\nEIGEN=%s\n' "$sdk_dir" "$root_dir" "$eigen_dir"
    cmake -S "$source_dir" -B "$output/build" -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -Dcorrectionlib_DIR="$sdk_dir" \
      -DROOT_DIR="$root_dir" -DEigen3_DIR="$eigen_dir" -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include"
    cmake --build "$output/build" --parallel 1
    for provider in "$output"/build/libatlas_muon*.so; do
        sha256sum "$provider"
        nm -D "$provider" | rg 'correctionlib_(plugin|register)'
        ldd "$provider"
    done
} 2>&1 | tee "$output/build.log"
