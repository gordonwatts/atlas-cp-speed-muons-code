#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?fresh output directory required}
sdk=${2:?pinned SDK venv required}
bridge=${3:?native bridge required}
provider=${4:?shared provider required}
payload=${5:?production payload required}
contract=${6:?frozen acceptance contract required}
reference="$campaign/results/checkpoint-02/full-observer-01"
export RELEASE_PYTHON RELEASE_PYTHONPATH
RELEASE_PYTHON=$(command -v python)
RELEASE_PYTHONPATH=${PYTHONPATH:-}
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
"${python[@]}" "$repo/atlas/muon/observe_framework.py" "$result" "$bridge" "$provider" "$payload" \
  "$campaign/results/checkpoint-02/status-build-04/libMuonEfficiencyStatusObserver.so"
"${python[@]}" "$repo/tools/muon_reference/reference_histogram.py" \
  "$result/reference.root" "$result/hist-reference.root" "$result/histogram.npz" --cross-check
"${python[@]}" "$repo/tools/muon_reference/compare_reference.py" "$reference" "$result/reference.root" \
  "$reference/histogram.npz" "$result/histogram.npz" "$contract" "$result/compare.json" \
  --candidate-systematics "$result/hist-reference.root"
