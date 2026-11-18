#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?fresh diagnostic path required}
install=${2:?checked final installation path required}
expected=${3:?accepted original histogram required}
demo="$install/extracted/muon-standalone/bundle/muon_demo.py"
cd "$install/neutral"
exec env -u PYTHONPATH -u PYTHONHOME -u LD_LIBRARY_PATH -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT \
    -u ROOTSYS -u ROOT_INCLUDE_PATH -u CMAKE_PREFIX_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV \
    PYTHONNOUSERSITE=1 "$install/pip-venv/bin/python" \
    "$repo/atlas/muon/measure_installed_demo.py" "$result" "$demo" "$INPUT" "$expected"
