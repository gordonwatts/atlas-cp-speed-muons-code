#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh M16 validation directory required}
contract=${2:?frozen acceptance required}
patch=${3:?reviewed reader patch required}
revision=${4:?clean immediate control revision required}
provider=${5:?unchanged accepted provider required}
validation=${6:?unchanged provider full B/C evidence required}
variant_kind=${7:-M16}
stock="$campaign/results/checkpoint-06"
ref="$campaign/results/checkpoint-03"
a="$campaign/results/checkpoint-02/full-observer-01"
sdk="$stock/stock-sdk-01/venv"
src="$repo/atlas/muon"
scripts="$repo/tools/muon_reference"
prod="$ref/export-03/muon-production.json"
trace="$ref/export-03/muon-diagnostic.json"
clean=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME -u CMAKE_PREFIX_PATH
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
python=("${clean[@]}" "$sdk/bin/python")
mkdir "$out" "$out/source" "$out/C" "$out/B"
{
    git -C "$repo" archive "$revision" -- atlas/muon | tar -x -C "$out/source"
    git -C "$out/source" apply --check "$patch"
    git -C "$out/source" apply "$patch"
    trial="$out/source/atlas/muon"
    driver="$trial/columnar/run_muons.py"
    if [[ "$variant_kind" == M09 ]]; then
        "${clean[@]}" PYTHONPATH="$trial/columnar" "$sdk/bin/python" \
            "$(dirname "$patch")/probe_primary.py" "$out/primary-transport.json"
    fi
    "${python[@]}" "$driver" observe "$INPUT" "$out/C/histogram.npz" --observations "$out/C/observations.root" \
        --provider "$provider" --momentum "$trace" --selection "$trace" --efficiency "$trace"
    "${python[@]}" "$scripts/compare_reference.py" "$a" "$out/C/observations.root" \
        "$a/histogram.npz" "$out/C/histogram.npz" "$contract" "$out/C/compare.json"
    "${python[@]}" "$scripts/root_free_stream_evidence.py" compare \
        "$validation/C/observations.root" "$out/C/observations.root" "$out/full-exact-stream.json"
    "${python[@]}" "$src/momentum/compare_release_momentum_trace.py" \
        "$ref/release-momentum-trace-01/trace.tsv" "$out/C/observations.root" "$out/release-primitives.json"
    cmp "$out/C/histogram.npz" "$validation/C/histogram.npz"
    cp "$validation/B/compare.json" "$validation/B/histogram.npz" "$out/B/"
    sha256sum "$provider" "$validation/B/compare.json" > "$out/unchanged-framework-witness.txt"
    opts=(--mode full --provider "$provider" --momentum "$prod" --selection "$prod" --efficiency "$prod")
    "${python[@]}" "$driver" histogram "$INPUT" "$out/chunk-173.npz" "${opts[@]}" --chunk-size 173
    cmp "$out/chunk-173.npz" "$validation/chunk-173.npz"
    "${python[@]}" "$driver" histogram "$INPUT" "$out/no-events.npz" "${opts[@]}" --stop 0
    cmp "$out/no-events.npz" "$validation/no-events.npz"
    if [[ "$variant_kind" == M16 ]]; then
        "${python[@]}" "$trial/audit_columnar_baskets.py" "$INPUT" "$out/basket-audit.json"
    "${python[@]}" - "$campaign/results/checkpoint-07/projected-basket-audit-02.json" "$out/basket-audit.json" \
         "$out/footprint-comparison.json" <<'PY'
import json
from pathlib import Path
import sys

a, b, out = map(Path, sys.argv[1:])
a, b = [json.loads(path.read_text()) for path in (a, b)]
removed = set(a["leaf_baskets"]) - set(b["leaf_baskets"])
assert len(removed) == 1
(name,) = removed
assert "MuonSpectrometerTrackParticles" in name and name.endswith(".theta")
assert not set(b["leaf_baskets"]) - set(a["leaf_baskets"])
delta = a["projected_compressed_bytes"] - b["projected_compressed_bytes"]
assert delta == a["leaf_baskets"][name]["compressed_bytes"] == 202056
with out.open("x") as output:
    json.dump({"removed_branch": name, "compressed_bytes_removed": delta,
               "control_bytes": a["projected_compressed_bytes"], "candidate_bytes": b["projected_compressed_bytes"],
               "fraction_removed": delta / a["projected_compressed_bytes"]}, output, indent=2)
    output.write("\n")
PY
    fi
    for part in stored nominal; do
        opts=(--mode "$part")
        if [[ "$part" == nominal ]]; then
            opts+=(--provider "$provider" --momentum "$prod" --selection "$prod" --efficiency "$prod")
        fi
        "${python[@]}" "$src/columnar/run_muons.py" histogram "$INPUT" "$out/control-$part.npz" "${opts[@]}"
        "${python[@]}" "$driver" histogram "$INPUT" "$out/candidate-$part.npz" "${opts[@]}"
        cmp "$out/control-$part.npz" "$out/candidate-$part.npz"
    done
    for variant in control candidate; do
        checker="$src/audit_all_native_momentum.py"
        if [[ "$variant" == candidate ]]; then checker="$trial/audit_all_native_momentum.py"; fi
        "${python[@]}" "$checker" capture "$provider" "$trace" "$INPUT" "$out/$variant-native.npz" "$out/$variant-all-lanes.json"
        cmp "$out/$variant-native.npz" "$a/histogram.npz"
    done
    "${python[@]}" "$src/audit_all_native_momentum.py" compare "$out/control-all-lanes.json" \
        "$out/candidate-all-lanes.json" "$out/all-lane-comparison.json"
} 2>&1 | tee "$out/job.log"
