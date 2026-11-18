#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
directory=${1:?fresh B result path required}
bridge=${2:?compiled B bridge path required}
export ATLAS_MUON_PROVIDER=${3:?shared provider path required}
export ATLAS_MUON_MOMENTUM=${4:?common production bundle required}
export ATLAS_MUON_SELECTION="$ATLAS_MUON_MOMENTUM"
export ATLAS_MUON_EFFICIENCY="$ATLAS_MUON_MOMENTUM"
export MUON_STATUS_OBSERVER_LIBRARY="$campaign/results/checkpoint-02/status-build-04/libMuonEfficiencyStatusObserver.so"
export ATLAS_MUON_FROZEN_CONFIG="$campaign/results/checkpoint-02/smoke-sf-status-01/full_config.json"
mode=${5:-audit}
events=${6:-100}
bash "$repo/atlas/muon/framework/run-framework.sh" "$bridge" "$directory" "$mode" "$events"
