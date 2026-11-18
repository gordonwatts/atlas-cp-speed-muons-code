#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh final profile directory required}
install=${2:?accepted installation result directory required}
expected=${3:?accepted original full histogram required}
minimum=${4:-60}
src="$repo/atlas/muon/profile"
demo="$install/extracted/muon-standalone/bundle/muon_demo.py"
uv="$campaign/results/checkpoint-02/columnar-bootstrap-01/uv"
python="$install/pip-venv/bin/python"
clean=(env -u PYTHONPATH -u PYTHONHOME -u LD_LIBRARY_PATH -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u CMAKE_PREFIX_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV
       PYTHONNOUSERSITE=1 UV_LINK_MODE=copy UV_SYSTEM_CERTS=true UV_NO_PROGRESS=1 RAYON_NUM_THREADS=1)
base=$("${clean[@]}" "$python" -c 'import sys; print(sys._base_executable)')
passes=$("${clean[@]}" "$python" "$src/profile_evidence.py" estimate-passes "$install/uv-repeat.stdout" --minimum-seconds "$minimum" --margin 1.25)
cd "$install/neutral"
"${clean[@]}" UV_CACHE_DIR="$install/uv-cache" ATLAS_PROFILE_PYTHON="$python" \
    bash "$src/capture_native.sh" "$out" "$expected" "$demo" "$passes" "$minimum" -- \
        "$uv" run --offline --no-project --python "$base" "$demo" "$INPUT" @OUTPUT@ -n @REPEAT@ --runtime-record @RUNTIME@
