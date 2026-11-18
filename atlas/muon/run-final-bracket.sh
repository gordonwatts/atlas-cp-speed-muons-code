#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh bracket result path required}
install=${2:?accepted extracted installation result path required}
bridge=${3:?verified framework bridge required}
provider=${4:?verified uninstrumented provider required}
payload=${5:?frozen shared payload required}
modes=${6:-stored,nominal,full}
python="$install/pip-venv/bin/python"
demo="$install/extracted/muon-standalone/bundle/muon_demo.py"
installed="$install/pip-venv/lib/python3.14/site-packages/atlas_muon_portable"
cmp "$provider" "$installed/native/libatlas_muon.so"
cmp "$payload" "$installed/payload.json"
export RELEASE_PYTHON RELEASE_PYTHONPATH CP_RUNNER
RELEASE_PYTHON=$(command -v python)
RELEASE_PYTHONPATH=${PYTHONPATH:-}
CP_RUNNER=$(command -v CPRun.py)
exec env -u PYTHONPATH -u PYTHONHOME "$python" "$repo/atlas/muon/benchmark_final.py" "$out" "$INPUT" \
    "$bridge" "$provider" "$payload" "$python" "$demo" \
    "$campaign/results/checkpoint-02/full-observer-01/histogram.npz" \
    "$campaign/results/checkpoint-02/production-series-01/block-02-stored/histogram.npz" --modes "$modes"
