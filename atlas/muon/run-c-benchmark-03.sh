#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?fresh timing directory required}
sdk=${2:?SDK venv required}
provider=${3:?shared provider required}
payload=${4:?production payload required}
accepted=${5:?accepted C histogram required}
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
"${python[@]}" "$repo/atlas/muon/benchmark_columnar.py" "$result" \
  "$repo/atlas/muon/columnar/run_muons.py" "$provider" "$payload" "$accepted" \
  "$campaign/results/checkpoint-02/production-series-01/block-02-stored/histogram.npz"
