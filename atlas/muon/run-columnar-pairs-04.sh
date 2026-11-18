#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
destination=${1:?fresh result path required}
sdk=${2:?pinned SDK venv required}
control=${3:?accepted uninstrumented control required}
candidate=${4:?uninstrumented candidate required}
payload=${5:?accepted shared production payload required}
expected=${6:?accepted output histogram required}
runtime=(env -u PYTHONPATH -u PYTHONHOME)
if [[ ${7:-release} == stock ]]; then
    runtime+=(-u LD_LIBRARY_PATH -u LD_PRELOAD -u ROOTSYS -u ROOT_INCLUDE_PATH -u CMAKE_PREFIX_PATH
              -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
fi
exec "${runtime[@]}" "$sdk/bin/python" "$repo/atlas/muon/benchmark_columnar_pairs.py" \
    "$destination" "$INPUT" "$control" "$candidate" "$payload" "$expected"
