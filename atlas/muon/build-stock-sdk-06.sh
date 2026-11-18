#!/usr/bin/env bash
set -euo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
output=${1:?fresh stock SDK result path required}
mkdir "$output"
cd "$repo"
uv="$campaign/results/checkpoint-02/columnar-bootstrap-01/uv"
python_bin="$campaign/columnar-reference-314/bin/python"
pin=3dcfa20ec01fb40c4d5fec58383785519dec1c00
export UV_SYSTEM_CERTS=true UV_CACHE_DIR="$output/uv-cache" UV_NO_PROGRESS=1
export UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_INSTALLS=1 UV_CONCURRENT_DOWNLOADS=1 RAYON_NUM_THREADS=1
export CMAKE_BUILD_PARALLEL_LEVEL=1 MAKEFLAGS=-j1 CC=/usr/bin/gcc CXX=/usr/bin/g++
clean=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME -u CMAKE_PREFIX_PATH
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
{
    git rev-parse HEAD "$pin"
    git merge-base --is-ancestor "$pin" HEAD
    git diff --exit-code "$pin" -- CMakeLists.txt pyproject.toml include src rapidjson pybind11
    git submodule status
    /usr/bin/g++ --version
    "${clean[@]}" "$uv" build --python "$python_bin" --wheel --out-dir "$output/wheels" -v
    "${clean[@]}" "$uv" venv --python "$python_bin" "$output/venv"
    wheel=("$output"/wheels/*.whl)
    test "${#wheel[@]}" = 1
    "${clean[@]}" "$uv" pip install --python "$output/venv/bin/python" "${wheel[0]}" \
        -r "$repo/tools/muon_reference/requirements-reference.txt"
    sha256sum "${wheel[0]}" > "$output/wheel-sha256.txt"
    "${clean[@]}" "$output/venv/bin/python" - <<'PY'
import correctionlib
import correctionlib._core as core
from pathlib import Path
import sys

print("PYTHON", sys.executable)
print("PACKAGE", correctionlib.__file__)
print("BINDING", core.__file__)
print("LIBRARY", Path(correctionlib.__file__).parent / "lib/libcorrectionlib.so")
print("VERSION", correctionlib.__version__)
active = Path("/proc/self/maps").read_text()
assert "/cvmfs/" not in active
print("\n".join(sorted({line.split()[-1] for line in active.splitlines()
                         if any(x in line for x in ("libcorrectionlib.so", "_core.cpython-", "libstdc++.so", "libgcc_s.so"))})))
PY
} 2>&1 | tee "$output/build.log"
