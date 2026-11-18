#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh trial verification path required}
contract=${2:?frozen acceptance contract required}
reference="$campaign/results/checkpoint-03"
trial="$campaign/results/checkpoint-04"
sdk="$reference/sdk-build-01/venv"
source_dir="$repo/atlas/muon"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
probe="$source_dir/momentum/probe_rng.py"
driver="$source_dir/columnar/run_muons.py"
control="$reference/provider-build-02/build/libatlas_muon.so"
candidate="$trial/m01-candidate-build-01/build/libatlas_muon.so"
instrumented="$trial/m01-instrumented-build-01/build/libatlas_muon.so"
bridge="$reference/framework-build-05/libAtlasMuonFramework.so"
prod="$reference/export-03/muon-production.json"
trace="$reference/export-03/muon-diagnostic.json"
frozen="$reference/c-acceptance-01/histogram.npz"
mkdir "$out"
{
    nm -D "$control" "$candidate" > "$out/uninstrumented-symbols.txt"
    if rg -q 'atlas_muon_m01_rng_' "$out/uninstrumented-symbols.txt"; then
        echo "A timed variant unexpectedly exports test instrumentation" >&2
        exit 1
    fi
    nm -D "$instrumented" | rg 'atlas_muon_m01_rng_' > "$out/probe-symbols.txt"
    test "$(wc -l < "$out/probe-symbols.txt")" -eq 4
    python - "$out/release-rng.json" <<'PY'
import json
from pathlib import Path
import struct
import sys
import ROOT

vectors = {}
for seed in (1001, 1001 + 8192, 1002):
    random = ROOT.TRandom3()
    random.SetSeed(seed)
    vectors[str(seed)] = [struct.pack(">d", random.Gaus(0, 1)).hex() for _ in range(6)]
with Path(sys.argv[1]).open("x") as output:
    json.dump({"root_version": str(ROOT.gROOT.GetVersion()), "vectors": vectors}, output, indent=2)
    output.write("\n")
PY
    for label in control candidate instrumented; do
        opts=()
        if [[ "$label" == instrumented ]]; then opts+=(--instrumented); fi
        "${python[@]}" "$probe" edges "${!label}" "$trace" "$source_dir/momentum/contract.json" \
            "$out/release-rng.json" "$out/$label-edges.json" "${opts[@]}"
    done
    "${python[@]}" "$probe" compare "$out/control-edges.json" "$out/candidate-edges.json" \
        "$out/instrumented-edges.json" "$out/edges-comparison.json"
    "${python[@]}" "$probe" campaign-counts "$instrumented" "$prod" "$driver" "$INPUT" \
        "$out/counts-histogram.npz" "$out/campaign-counts.json"
    cmp "$out/counts-histogram.npz" "$frozen"
    bash "$source_dir/run-c-full-03.sh" "$out/C" "$sdk" "$candidate" "$trace" "$contract"
    bash "$source_dir/run-b-full-03.sh" "$out/B" "$sdk" "$bridge" "$candidate" "$prod" "$contract"
    "${python[@]}" "$source_dir/momentum/compare_release_momentum_trace.py" \
        "$reference/release-momentum-trace-01/trace.tsv" "$out/C/observations.root" "$out/release-primitives.json"
    cmp "$out/C/histogram.npz" "$out/B/histogram.npz"
    cmp "$out/C/histogram.npz" "$frozen"
    opts=(--mode full --provider "$candidate" --momentum "$prod" --selection "$prod" --efficiency "$prod")
    "${python[@]}" "$driver" histogram "$INPUT" "$out/chunk-173.npz" "${opts[@]}" --chunk-size 173
    cmp "$out/chunk-173.npz" "$frozen"
    "${python[@]}" "$driver" histogram "$INPUT" "$out/no-events.npz" "${opts[@]}" --stop 0
    cmp "$out/no-events.npz" "$reference/boundary-checks-01/no-events.npz"
    sha256sum "$control" "$candidate" "$instrumented" "$prod" "$trace"\
        "$out/"*.json "$out/"*.npz "$out/C/compare.json" "$out/C/histogram.npz" \
        "$out/B/compare.json" "$out/B/histogram.npz" > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
