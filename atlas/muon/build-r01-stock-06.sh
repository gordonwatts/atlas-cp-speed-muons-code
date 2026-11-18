#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh stock provider result path required}
sdk=${2:?stock SDK venv path required}
data=${3:?accepted parity fixtures path required}
oracle=${4:?real release parity output required}
src=${5:-$repo/atlas/muon}
compile_flags=${6:-}
sdk_dir="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
externals="${AtlasArea/AnalysisBase\//AnalysisBaseExternals\/}/InstallArea/$CMTCONFIG"
eigen=$(dirname "$(find "$externals" -iname Eigen3Config.cmake -print -quit)")
clean=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME -u CMAKE_PREFIX_PATH
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
mkdir "$out"
{
    /usr/bin/g++ --version
    "${clean[@]}" /usr/bin/cmake -S "$src" -B "$out/provider-build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DCMAKE_CXX_FLAGS="$compile_flags" \
        -Dcorrectionlib_DIR="$sdk_dir" -DEigen3_DIR="$eigen" -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include" \
        -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON '-DCMAKE_INSTALL_RPATH=$ORIGIN/../../correctionlib/lib'
    "${clean[@]}" /usr/bin/cmake --build "$out/provider-build" --parallel 1 --target atlas_muon
    provider="$out/provider-build/libatlas_muon.so"
    readelf -d "$provider" > "$out/provider-dynamic.txt"
    "${clean[@]}" /usr/bin/cmake -S "$src/momentum/root_free_probe" -B "$out/parity-build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -DATLAS_R01_BUILD_ROOT_ORACLE=OFF -DATLAS_R01_HEADER_DIR="$src/momentum"
    "${clean[@]}" /usr/bin/cmake --build "$out/parity-build" --parallel 1
    "${clean[@]}" "$out/parity-build/r01_standalone" "$data/seeds.txt" "$data/axes.txt" > "$out/standalone.tsv"
    if ! cmp "$oracle" "$out/standalone.tsv"; then
        diff -u "$oracle" "$out/standalone.tsv" | head -50 || true
        exit 1
    fi
    echo "Stock compiler primitive output matches release ROOT byte for byte"
    "${clean[@]}" "$sdk/bin/python" - "$provider" "$out/build-id.txt" <<'PY'
import ctypes
from pathlib import Path
import sys
from correctionlib import plugins

provider = Path(sys.argv[1])
plugins.load_library(provider)
library = ctypes.CDLL(str(provider))
library.correctionlib_plugin_build_id.restype = ctypes.c_char_p
library.correctionlib_plugin_api_version.restype = ctypes.c_int
assert library.correctionlib_plugin_api_version() == 4
build_id = library.correctionlib_plugin_build_id().decode()
print(build_id)
Path(sys.argv[2]).write_text(build_id + "\n")
maps = Path("/proc/self/maps").read_text()
assert "/cvmfs/" not in maps and str(provider) in maps
for line in sorted({item.split()[-1] for item in maps.splitlines() if ".so" in item}):
    print(line)
PY
    sha256sum "$provider" "$out/standalone.tsv" "$oracle" > "$out/artifact-sha256.txt"
    "${clean[@]}" ldd "$out/parity-build/r01_standalone" > "$out/standalone-closure.txt"
} 2>&1 | tee "$out/build.log"
