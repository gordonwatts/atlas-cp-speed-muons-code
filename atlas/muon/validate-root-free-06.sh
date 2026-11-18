#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh verification directory required}
contract=${2:?frozen acceptance required}
sdk=${3:?paired SDK venv required}
provider=${4:?ROOT-free provider required}
bridge=${5:?matching release framework bridge required}
ref="$campaign/results/checkpoint-03"
control="$campaign/results/checkpoint-05/m11-validation-01"
src="$repo/atlas/muon"
prod="$ref/export-03/muon-production.json"
trace="$ref/export-03/muon-diagnostic.json"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
mkdir "$out"
{
    bash "$src/run-c-full-03.sh" "$out/C" "$sdk" "$provider" "$trace" "$contract"
    bash "$src/run-b-full-03.sh" "$out/B" "$sdk" "$bridge" "$provider" "$prod" "$contract"
    "${python[@]}" "$src/momentum/compare_release_momentum_trace.py" \
        "$ref/release-momentum-trace-01/trace.tsv" "$out/C/observations.root" "$out/release-primitives.json"
    "${python[@]}" "$repo/tools/muon_reference/root_free_stream_evidence.py" compare \
        "$control/C/observations.root" "$out/C/observations.root" "$out/full-exact-stream.json"
    cmp "$out/C/histogram.npz" "$out/B/histogram.npz"
    cmp "$out/C/histogram.npz" "$control/C/histogram.npz"
    opts=(--mode full --provider "$provider" --momentum "$prod" --selection "$prod" --efficiency "$prod")
    driver="$src/columnar/run_muons.py"
    "${python[@]}" "$driver" histogram "$INPUT" "$out/chunk-173.npz" "${opts[@]}" --chunk-size 173
    cmp "$out/chunk-173.npz" "$control/chunk-173.npz"
    "${python[@]}" "$driver" histogram "$INPUT" "$out/no-events.npz" "${opts[@]}" --stop 0
    cmp "$out/no-events.npz" "$control/no-events.npz"
    sha256sum "$provider" "$bridge" "$prod" "$trace" "$out/"*.{json,npz} \
        "$out/C/compare.json" "$out/B/compare.json" "$out/C/histogram.npz" "$out/B/histogram.npz" \
        > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
