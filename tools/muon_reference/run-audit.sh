#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
smoke_dir="$RESULTS/${1:?smoke identifier required}"
shift
python "$reference_dir/smoke_evidence.py" "$smoke_dir" "${@/#/$RESULTS/}" 2>&1 | tee "$smoke_dir/smoke-evidence.log"
