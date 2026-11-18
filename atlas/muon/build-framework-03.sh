#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
output=${1:?fresh framework build path required}
sdk=${2:?SDK venv required}
package="$sdk/lib/python3.14/site-packages/correctionlib"
export ATLAS_MUON_CORE_LIBRARY="$package/lib/libcorrectionlib.so"
export ATLAS_MUON_CORE_INCLUDE="$package/include"
bash "$repo/atlas/muon/framework/build-framework.sh" "$output"
