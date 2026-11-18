#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
target=${1:?fresh standalone result path required}
sdk=${2:?stock SDK result path required}
provider=${3:?accepted ROOT-free provider required}
validation=${4:?accepted full B/C validation path required}
licenses=${5:?source notices and license directory required}
version=${6:-0.1.dev2}
ref="$campaign/results/checkpoint-03"
source_dir="$repo/atlas/muon"
origin="$source_dir/portable"
payload="$ref/export-03/muon-production.json"
contract="$campaign/results/checkpoint-02/full-observer-01/histogram.npz"
stored="$campaign/results/checkpoint-02/production-series-01/block-02-stored/histogram.npz"
core=( "$sdk"/wheels/*.whl )
test "${#core[@]}" = 1
clean=(env -u PYTHONPATH -u PYTHONHOME -u LD_LIBRARY_PATH -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u CMAKE_PREFIX_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV
       PYTHONNOUSERSITE=1 UV_LINK_MODE=copy UV_SYSTEM_CERTS=true UV_NO_PROGRESS=1 RAYON_NUM_THREADS=1)
build_python=( "${clean[@]}" "$sdk/venv/bin/python" )
mkdir "$target"
mkdir "$target/neutral" "$target/assembled" "$target/extracted"
assembly="$target/assembled/muon-standalone"
mkdir "$assembly" "$assembly/source" "$assembly/validation"
{
    "${build_python[@]}" - "$repo" "$ref" "$validation" "$target" "$provider" "${core[0]}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

repo, ref, validation, target, provider, core = map(Path, sys.argv[1:])
src = repo / "atlas/muon"
def identity(path):
    with path.open("rb") as handle:
        return {"file": path.name, "sha256": hashlib.file_digest(handle, "sha256").hexdigest()}

comparisons = {mode: json.loads((validation / mode / "compare.json").read_text()) for mode in ("B", "C")}
assert all(item["passed"] and item["values_compared"] == 12_732_453 for item in comparisons.values())
draws = json.loads((validation / "full-exact-stream.json").read_text())
assert draws["passed"] and draws["bits_compared"] == 3_044_391
release_primitives = json.loads((validation / "release-primitives.json").read_text())
assert release_primitives["passed"]
receipt = {
    "sample": {"uuid": comparisons["C"]["source_uuid"], "entries": 20_000, "objects": 14_997,
               "states": 97, "sha256": "017e408002b0153df1de695d8c2ace325e0731b658cbd5ca695a349eb0a059c0"},
    "original_release": {"version": "AnalysisBase 25.2.117", "commit": "6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c"},
    "provider": identity(provider), "core": identity(core),
    "physics": {mode: identity(validation / mode / "compare.json") for mode in comparisons},
    "original_stream": identity(validation / "full-exact-stream.json"),
    "release_primitives": identity(validation / "release-primitives.json"),
    "histogram": identity(validation / "C" / "histogram.npz"),
}
roles = {
    "release": receipt["original_release"],
    "momentum": [identity(src / "momentum" / name) for name in ("momentum.cxx", "momentum_payload.cxx", "root_compat.h", "contract.json")]
    + [identity(ref / "export-03" / "momentum-production.provenance.json")],
    "selection": [identity(src / "selection" / name) for name in ("selection.cc", "selection.json")],
    "efficiency": [identity(src / "efficiency" / "efficiency.cc"),
                   identity(ref / "export-03" / "efficiency-provenance.json")],
}
for name, document in (("acceptance.json", receipt), ("source-roles.json", roles)):
    with (target / name).open("x") as output:
        json.dump(document, output, sort_keys=True, indent=2)
        output.write("\n")
PY
    abi=$("${build_python[@]}" - "$provider" <<'PY'
import ctypes
from pathlib import Path
import sys
from correctionlib import plugins
path = Path(sys.argv[1])
plugins.load_library(path)
native = ctypes.CDLL(str(path))
native.correctionlib_plugin_build_id.restype = ctypes.c_char_p
print(native.correctionlib_plugin_build_id().decode())
PY
    )
    revision=$(git -C "$repo" rev-parse HEAD)
    args=(--core-wheel "${core[0]}" --provider "$provider" --payload "$payload" --workflow "$source_dir/columnar/workflow.json"
          --raw-reader "$source_dir/columnar/raw_input.py" --source-roles "$target/source-roles.json" --acceptance "$target/acceptance.json")
    "${build_python[@]}" "$origin/artifact_identity.py" "$target/identity.json" --profile full \
        --core-commit 3dcfa20ec01fb40c4d5fec58383785519dec1c00 --plugin-build-id "$abi" \
        --compiler "$(/usr/bin/g++ -dumpfullversion)" --build-python 3.14.10 \
        --original-stream-checkpoint a54ceb3f1a30ba39b3cb3a819239575238789c1e --delivery-checkpoint "$revision" \
        --rng-stream ROOT-6.40.02-TRandom3-Gaus "${args[@]}"
    "${build_python[@]}" "$origin/assemble.py" --identity "$target/identity.json" --version "$version" \
        --output "$assembly/bundle" --public-pins "$repo/tools/muon_reference/requirements-reference.txt" "${args[@]}"

    git -C "$repo" archive --format=tar.gz --prefix=atlas-muon-source/ \
        --output="$assembly/source/atlas-muon-corresponding-source.tar.gz" "$revision" atlas/muon tools/muon_reference
    git -C "$repo" submodule status > "$assembly/source/submodule-pins.txt"
    cp "$repo/.gitmodules" "$assembly/source/correctionlib-submodules.txt"
    printf '%s\n' "Provider source commit $revision" \
        'Pinned CorrectionLib core https://github.com/gordonwatts/correctionlib/tree/3dcfa20ec01fb40c4d5fec58383785519dec1c00' \
        'Release tool source https://gitlab.cern.ch/atlas/athena/-/tree/6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c' \
        'ROOT numeric source https://github.com/root-project/root/tree/d4c0003542952bec9c4edc86c419784a32875e9d' \
        > "$assembly/source/references.txt"
    cp "$licenses/"* "$assembly/source/"
    cp "$target/acceptance.json" "$validation/full-exact-stream.json" "$validation/release-primitives.json" "$assembly/validation/"
    cp "$validation/B/compare.json" "$assembly/validation/B-versus-release.json"
    cp "$validation/C/compare.json" "$assembly/validation/C-versus-release.json"
    {
        cat <<'EOF'
Run on Linux x86-64 with CPython 3.14.10 and a compatible glibc 2.34 system runtime.
Set INPUT to the original PHYSLITE path or a byte-identical local copy.
From this directory use either of these commands for the full histogram.

uv run --no-project --python 3.14.10 bundle/muon_demo.py "$INPUT" histogram.npz

python3.14 -m venv muon-venv
muon-venv/bin/python -m pip install -r bundle/public-requirements.txt bundle/wheels/*.whl
muon-venv/bin/python bundle/muon_demo.py "$INPUT" histogram.npz

Use a fresh output filename. Optional flags include --mode nominal, --mode stored,
--stop 100 and -n 2. Public dependency wheels are resolved from the normal package
index on a cold install. The two project wheels are included. The event file and
the Python interpreter are supplied separately. bundle/bundle.json fixes the
assembly file list and SHA-256 values.
EOF
        printf '\nOriginal input path\n%s\n' "$INPUT"
    } > "$assembly/USAGE.txt"
    (
        cd "$assembly"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
    archive="$target/atlas-muon-standalone-$version-cp314-linux_x86_64.zip"
    (
        cd "$target/assembled"
        "${build_python[@]}" -m zipfile -c "$archive" muon-standalone
    )
    "${build_python[@]}" -m zipfile -t "$archive"
    "${build_python[@]}" -m zipfile -e "$archive" "$target/extracted"
    extracted="$target/extracted/muon-standalone"
    (cd "$extracted"; sha256sum -c SHA256SUMS)
    sha256sum "$archive" > "$target/archive-sha256.txt"

    bundle="$extracted/bundle"
    demo="$bundle/muon_demo.py"
    base=$("${build_python[@]}" -c 'import sys; print(sys._base_executable)')
    uv="$campaign/results/checkpoint-02/columnar-bootstrap-01/uv"
    cd "$target/neutral"
    "${clean[@]}" "$base" --version
    "${clean[@]}" "$uv" --version
    "${clean[@]}" "$base" "$bundle/audit_install.py" "$bundle" "$target/bundle-audit.json"
    uv_cmd=("${clean[@]}" UV_CACHE_DIR="$target/uv-cache" "$uv" run --no-project --python "$base" "$demo")
    "${uv_cmd[@]}" "$INPUT" "$target/uv-full.npz" --runtime-record "$target/uv-runtime.json" > "$target/uv-full.stdout"
    cmp "$target/uv-full.npz" "$contract"
    "${clean[@]}" "$base" "$bundle/audit_install.py" "$bundle" "$target/uv-audit.json" \
        --runtime-record "$target/uv-runtime.json" --cache "$target/uv-cache" --cpu 18

    "${clean[@]}" "$base" -m venv "$target/pip-venv"
    pip_python="$target/pip-venv/bin/python"
    "${clean[@]}" PIP_CACHE_DIR="$target/pip-cache" "$pip_python" -m pip install --only-binary=:all: \
        --report "$target/pip-install.json" -r "$bundle/public-requirements.txt" "$bundle"/wheels/*.whl
    "${clean[@]}" "$pip_python" -m pip check
    "${clean[@]}" "$pip_python" -m pip inspect > "$target/pip-inspect.json"
    pip_cmd=("${clean[@]}" "$pip_python" "$demo")
    "${pip_cmd[@]}" "$INPUT" "$target/pip-full.npz" --runtime-record "$target/pip-runtime.json" > "$target/pip-full.stdout"
    cmp "$target/pip-full.npz" "$contract"
    "${clean[@]}" "$base" "$bundle/audit_install.py" "$bundle" "$target/pip-audit.json" \
      --runtime-record "$target/pip-runtime.json" --cache "$target/pip-cache" --cpu 18
    for mode in stored nominal; do
        "${uv_cmd[@]}" "$INPUT" "$target/uv-$mode.npz" --mode "$mode" \
            --runtime-record "$target/uv-$mode-runtime.json" > "$target/uv-$mode.stdout"
        "${pip_cmd[@]}" "$INPUT" "$target/pip-$mode.npz" --mode "$mode" \
            --runtime-record "$target/pip-$mode-runtime.json" > "$target/pip-$mode.stdout"
        cmp "$target/uv-$mode.npz" "$target/pip-$mode.npz"
    done
    cmp "$target/uv-stored.npz" "$stored"
    "${uv_cmd[@]}" "$INPUT" "$target/uv-repeat.npz" -n 2 > "$target/uv-repeat.stdout"
    cmp "$target/uv-repeat.npz" "$contract"
    "${uv_cmd[@]}" "$INPUT" "$target/uv-empty.npz" --stop 0 > "$target/uv-empty.stdout"
    cmp "$target/uv-empty.npz" "$validation/no-events.npz"
    for invalid in 0 -1 1.5; do
        if "${uv_cmd[@]}" "$INPUT" "$target/invalid.npz" -n "$invalid" > "$target/rejected-$invalid.log" 2>&1; then
            exit 1
        fi
        test ! -e "$target/invalid.npz"
    done
    "${clean[@]}" "$pip_python" - "$target" "$contract" <<'PY'
import json
from pathlib import Path
import sys
import numpy as np

target, reference = map(Path, sys.argv[1:])
with np.load(reference, allow_pickle=False) as expected, np.load(target / "uv-nominal.npz", allow_pickle=False) as got:
    assert got["states"].tolist() == ["NOSYS"]
    np.testing.assert_array_equal(expected["pt_edges_GeV"], got["pt_edges_GeV"])
    for field in ("count", "sumw", "sumw2"):
        np.testing.assert_array_equal(expected[field][:, :1], got[field])
for mode in ("uv", "pip"):
    data = json.loads((target / f"{mode}-stored-runtime.json").read_text())
    maps = "\n".join(data["maps"])
    assert not any(name in maps for name in ("libatlas_muon", "libcorrectionlib", "/cvmfs/"))
    assert data["threads"] == 1 and data["cpu"] == [18]
print("Both clean installations matched all three modes, repeated output, empty and invalid repeat options")
PY
    sha256sum "$target/"*.npz > "$target/output-sha256.txt"
} 2>&1 | tee "$target/job.log"
