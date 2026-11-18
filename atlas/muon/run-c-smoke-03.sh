#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
destination=${1:?fresh smoke path required}
sdk=${2:?SDK venv required}
provider=${3:?validated provider path required}
production=${4:?production bundle required}
diagnostic=${5:?diagnostic bundle required}
mkdir "$destination"
runner="$repo/atlas/muon/columnar/run_muons.py"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
{
    "${python[@]}" "$runner" histogram "$INPUT" "$destination/nominal.npz" --mode nominal --stop 100 \
      --provider "$provider" --momentum "$production" --selection "$production" --efficiency "$production"
    "${python[@]}" "$runner" observe "$INPUT" "$destination/full.npz" --stop 100 \
      --observations "$destination/observations.root" --provider "$provider" \
      --momentum "$diagnostic" --selection "$diagnostic" --efficiency "$diagnostic"
    "${python[@]}" "$repo/tools/muon_reference/reference_histogram.py" \
      "$campaign/results/checkpoint-02/smoke-sf-status-01/reference.root" \
      "$campaign/results/checkpoint-02/smoke-sf-status-01/hist-reference.root" "$destination/original-a.npz" --cross-check
    sha256sum "$destination"/*.{npz,root}
} 2>&1 | tee "$destination/job.log"
