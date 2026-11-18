#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
destination=${1:?result path required}
oracle=${2:?full-observer directory required}
shift 2
export RELEASE_PYTHON
RELEASE_PYTHON=$(command -v python)
export RELEASE_PYTHONPATH=${PYTHONPATH:-}
export CP_RUNNER
CP_RUNNER=$(command -v CPRun.py)
hist_python="$CAMPAIGN/columnar-reference-314/bin/python"
env -u PYTHONHOME -u PYTHONPATH "$hist_python" "$reference_dir/benchmark_reference.py" "$destination" "$oracle" "$@"
