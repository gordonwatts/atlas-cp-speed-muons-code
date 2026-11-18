#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?fresh timing directory required}
sdk=${2:?SDK venv required}
bridge=${3:?native framework bridge required}
provider=${4:?shared provider required}
payload=${5:?production payload required}
accepted=${6:?accepted B result directory required}
export RELEASE_PYTHON RELEASE_PYTHONPATH CP_RUNNER
RELEASE_PYTHON=$(command -v python)
RELEASE_PYTHONPATH=${PYTHONPATH:-}
CP_RUNNER=$(command -v CPRun.py)
env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python" "$repo/atlas/muon/benchmark_framework.py" \
  "$result" "$bridge" "$provider" "$payload" "$accepted"
