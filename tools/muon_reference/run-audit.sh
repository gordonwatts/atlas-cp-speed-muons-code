#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
smoke_dir="$RESULTS/${1:?smoke identifier required}"
args=("$smoke_dir")
if [[ "$#" -ge 2 ]]; then
    comparison=$2
    [[ "$comparison" = /* ]] || comparison="$RESULTS/$comparison"
    args+=("$comparison")
fi
python "$reference_dir/smoke_evidence.py" "${args[@]}" 2>&1 | tee "$smoke_dir/smoke-evidence.log"
