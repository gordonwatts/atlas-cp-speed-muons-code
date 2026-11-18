#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
source_dir="$repo/atlas/muon"
discovery="$campaign/results/checkpoint-01"
sdk_venv=${2:?pinned SDK venv required}
output=${1:?fresh payload path required}
mkdir "$output"
export PYTHONPATH="$campaign/results/export-preflight-01/python-deps:$PYTHONPATH"
{
    python "$source_dir/efficiency/export_efficiency.py" "$discovery/resolved-calibration-data.json" \
      "$discovery/initialized-tools-allsys-eta.json" "$discovery/smoke-allsys-eta-02/smoke-evidence.json" \
      "$output/efficiency.json" "$output/efficiency-provenance.json"
    for role in production diagnostic; do
        args=()
        if [[ "$role" == diagnostic ]]; then args=(--diagnostics); fi
        python "$source_dir/momentum/export_momentum.py" --inventory "$discovery/resolved-calibration-data.json" \
          --discovery "$discovery/initialized-tools-allsys-eta.json" --payload "$output/momentum-$role.json" \
          --provenance "$output/momentum-$role.provenance.json" "${args[@]}"
        env -u PYTHONHOME -u PYTHONPATH "$sdk_venv/bin/python" "$source_dir/merge_payloads.py" \
          "$output/muon-$role.json" "$output/momentum-$role.json" "$source_dir/selection/selection.json" "$output/efficiency.json"
    done
    env -u PYTHONHOME -u PYTHONPATH "$sdk_venv/bin/python" "$source_dir/merge_payloads.py" \
      "$output/selection-efficiency.json" "$source_dir/selection/selection.json" "$output/efficiency.json"
    find "$output" -name '*.json' -type f -print0 | sort -z | xargs -0 sha256sum > "$output/sha256.txt"
} 2>&1 | tee "$output/export.log"
