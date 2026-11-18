#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
output=${1:?fresh SDK result path required}
source "$repo/tools/muon_reference/environment.sh"
mkdir "$output"
cd "$repo"
uv="$campaign/results/checkpoint-02/columnar-bootstrap-01/uv"
python_bin="$campaign/columnar-reference-314/bin/python"
export UV_SYSTEM_CERTS=true
export UV_CACHE_DIR="$output/uv-cache"
export UV_NO_PROGRESS=1
export CMAKE_BUILD_PARALLEL_LEVEL=1
export MAKEFLAGS=-j1
export CC=gcc CXX=g++
{
    git rev-parse 3dcfa20ec01fb40c4d5fec58383785519dec1c00
    git -c pack.threads=1 submodule update --init --jobs 1
    git submodule status
    command -v gcc cmake ninja
    gcc --version
    "$python_bin" --version
    env -u PYTHONHOME -u PYTHONPATH "$uv" build --python "$python_bin" --wheel --out-dir "$output/wheels" -v
    env -u PYTHONHOME -u PYTHONPATH "$uv" venv --python "$python_bin" "$output/venv"
    wheels=( "$output"/wheels/*.whl )
    test "${#wheels[@]}" = 1
    env -u PYTHONHOME -u PYTHONPATH "$uv" pip install --python "$output/venv/bin/python" "${wheels[0]}" -r "$repo/tools/muon_reference/requirements-reference.txt"
    sha256sum "${wheels[0]}"
    env -u PYTHONHOME -u PYTHONPATH "$output/venv/bin/python" - <<'PY'
import correctionlib
import correctionlib._core as core
from pathlib import Path
import sys
print('PYTHON', sys.executable)
print('PACKAGE', correctionlib.__file__)
print('BINDING', core.__file__)
print('LIBRARY', Path(correctionlib.__file__).parent / 'lib/libcorrectionlib.so')
print('VERSION', correctionlib.__version__)
with open('/proc/self/maps') as maps:
    print('\n'.join(sorted({line.split()[-1] for line in maps if 'libcorrectionlib.so' in line or '_core.cpython-' in line})))
PY
} 2>&1 | tee "$output/build.log"
