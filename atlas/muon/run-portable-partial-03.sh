#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
target=${1:?fresh partial-package result path required}
sdk=${2:?pinned SDK venv required}
payload=${3:?checked selection-and-efficiency payload required}
mkdir "$target"
src="$repo/atlas/muon"
origin="$src/portable"
sdk_install="$sdk/lib/python3.14/site-packages/correctionlib"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
wheel=("$sdk"/../wheels/*.whl)
test "${#wheel[@]}" = 1
export CMAKE_BUILD_PARALLEL_LEVEL=1
{
    cmake -S "$origin" -B "$target/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER="$(command -v g++)" \
      -Dcorrectionlib_DIR="$sdk_install/cmake" -DATLAS_MUON_SELECTION_SOURCE="$src/selection" \
      -DATLAS_MUON_EFFICIENCY_SOURCE="$src/efficiency"
    cmake --build "$target/build" --parallel 1
    provider="$target/build/libatlas_muon.so"
    export LD_LIBRARY_PATH="$sdk_install/lib:$LD_LIBRARY_PATH"
    abi=$("${python[@]}" -c 'import ctypes, sys; lib=ctypes.CDLL(sys.argv[1]); f=lib.correctionlib_plugin_build_id; f.restype=ctypes.c_char_p; print(f().decode())' "$provider")
    inputs=(--core-wheel "${wheel[0]}" --provider "$provider" --payload "$payload" --workflow "$src/columnar/workflow.json" --raw-reader "$src/columnar/raw_input.py")
    "${python[@]}" - "$src" "$target" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
src, output = map(Path, sys.argv[1:])
roles = {}
for key, paths in {"selection": (src / "selection/selection.cc", src / "selection/selection.json"),
                   "efficiency": (src / "efficiency/efficiency.cc", src.parents[1].parent / "results/checkpoint-03/export-03/efficiency-provenance.json")}.items():
    roles[key] = [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
(output / "source-roles.json").write_text(json.dumps(roles, indent=2) + "\n")
PY
    inputs+=(--source-roles "$target/source-roles.json")
    "${python[@]}" "$origin/artifact_identity.py" "$target/identity.json" --profile partial-selection-efficiency \
      --core-commit 3dcfa20ec01fb40c4d5fec58383785519dec1c00 --plugin-build-id "$abi" \
      --compiler "$(g++ -dumpfullversion)" --build-python 3.14.10 "${inputs[@]}"
    "${python[@]}" "$origin/assemble.py" --identity "$target/identity.json" --version 0.1.dev1 \
      --output "$target/bundle" --public-pins "$repo/tools/muon_reference/requirements-reference.txt" "${inputs[@]}"

    bundle="$target/bundle"
    base=$("${python[@]}" -c 'import sys; print(sys._base_executable)')
    uv="$campaign/results/checkpoint-02/columnar-bootstrap-01/uv"
    clean=(env -u PYTHONPATH -u PYTHONHOME -u LD_LIBRARY_PATH -u LD_PRELOAD -u ROOTSYS -u ROOT_INCLUDE_PATH \
        -u CMAKE_PREFIX_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1 UV_LINK_MODE=copy UV_SYSTEM_CERTS=true UV_NO_PROGRESS=1)
    mkdir "$target/neutral"
    cd "$target/neutral"
    "${clean[@]}" "$base" "$bundle/audit_install.py" "$bundle" "$target/bundle-audit.json"
    entry="$bundle/partial_probe.py"
    external=()
    if "${clean[@]}" UV_CACHE_DIR="$target/uv-cache-clean" "$uv" run --no-project --python "$base" \
      "$entry" "$INPUT" "$target/uv-result-clean" --entries 100 --runtime-record "$target/uv-runtime-clean.json" \
        > "$target/clean-uv.log" 2>&1; then
        cache="$target/uv-cache-clean"
        record="$target/uv-runtime-clean.json"
    else
        cat "$target/clean-uv.log"
        if ! rg -q 'GLIBCXX_|CXXABI_|libstdc\+\+' "$target/clean-uv.log"; then exit 1; fi
        mkdir "$target/gnu-runtime"
        for soname in libstdc++.so.6 libgcc_s.so.1; do
            original=$(g++ -print-file-name="$soname")
            cp --dereference "$original" "$target/gnu-runtime/$soname"
            sha256sum "$original" "$target/gnu-runtime/$soname"
            external+=(--external-gnu-runtime "$target/gnu-runtime/$soname")
        done
        clean+=(LD_LIBRARY_PATH="$target/gnu-runtime")
        cache="$target/uv-cache-local-runtime"
        record="$target/uv-runtime-local.json"
        "${clean[@]}" UV_CACHE_DIR="$cache" "$uv" run --no-project --python "$base" \
          "$entry" "$INPUT" "$target/uv-result-local" --entries 100 --runtime-record "$record"
    fi
    "${clean[@]}" "$base" "$bundle/audit_install.py" "$bundle" "$target/uv-audit.json" --runtime-record "$record" --cache "$cache" --cpu 18 "${external[@]}"
    "${clean[@]}" "$base" -m venv "$target/pip-venv"
    pip_python="$target/pip-venv/bin/python"
    "${clean[@]}" PIP_CACHE_DIR="$target/pip-cache" "$pip_python" -m pip install --only-binary=:all: \
      --report "$target/pip-install.json" -r "$bundle/public-requirements.txt" "$bundle"/wheels/*.whl
    "${clean[@]}" "$pip_python" -m pip check
    "${clean[@]}" "$pip_python" "$entry" "$INPUT" "$target/pip-result" --entries 100 --runtime-record "$target/pip-runtime.json"
    "${clean[@]}" "$base" "$bundle/audit_install.py" "$bundle" "$target/pip-audit.json" --runtime-record "$target/pip-runtime.json" --cache "$target/pip-cache" --cpu 18 "${external[@]}"
} 2>&1 | tee "$target/job.log"
