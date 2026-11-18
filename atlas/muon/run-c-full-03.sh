#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
destination=${1:?fresh result path required}
sdk=${2:?pinned SDK venv required}
provider=${3:?provider path required}
payload=${4:?diagnostic payload required}
acceptance=${5:?frozen acceptance required}
event_chunk=${6:-2000}
mkdir "$destination"
reference="$campaign/results/checkpoint-02/full-observer-01"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
{
    "${python[@]}" "$repo/atlas/muon/columnar/run_muons.py" observe "$INPUT" "$destination/histogram.npz" \
      --observations "$destination/observations.root" --provider "$provider" \
      --momentum "$payload" --selection "$payload" --efficiency "$payload" --chunk-size "$event_chunk"
    "${python[@]}" "$repo/tools/muon_reference/compare_reference.py" "$reference" "$destination/observations.root" \
      "$reference/histogram.npz" "$destination/histogram.npz" "$acceptance" "$destination/compare.json"
    sha256sum "$destination"/*.{npz,root,json}
} 2>&1 | tee "$destination/job.log"
