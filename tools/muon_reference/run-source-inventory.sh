#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
python "$reference_dir/release_source_inventory.py" "$RESULTS" "$CAMPAIGN/results/checkpoint-01/resolved-calibration-data.json" "$@"
