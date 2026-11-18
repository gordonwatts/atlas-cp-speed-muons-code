#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?fresh output path required}
sdk=${2:?SDK venv required}
provider=${3:?shared provider required}
payload=${4:?production payload required}
accepted=${5:?accepted C histogram required}
mkdir "$result"
env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python" "$repo/atlas/muon/measure_columnar_phases.py" \
  "$INPUT" "$provider" "$payload" "$result/histogram.npz" "$result/phases.json" "$accepted" \
  2>&1 | tee "$result/job.log"
