#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh M25 trial directory required}
contract=${2:?frozen acceptance required}
reviewed=${3:?reviewed M25 sources required}
revision=${4:?clean immediate control revision required}
control=${5:?accepted immediate control binary required}
stock="$campaign/results/checkpoint-06"
ref="$campaign/results/checkpoint-03"
sdk="$stock/stock-sdk-01/venv"
fixtures="$stock/parity-data-01"
oracle="$stock/r01-gcc15-build-01/root.tsv"
bridge="$stock/r01-stock-bridge-01/libAtlasMuonFramework.so"
prod="$ref/export-03/muon-production.json"
trace="$ref/export-03/muon-diagnostic.json"
frozen="$ref/c-acceptance-01/histogram.npz"
src="$repo/atlas/muon"
clean=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME -u CMAKE_PREFIX_PATH
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
python=("${clean[@]}" "$sdk/bin/python")
mkdir "$out" "$out/control-source" "$out/source"
{
    git -C "$repo" archive "$revision" -- atlas/muon | tar -x -C "$out/control-source"
    git -C "$repo" archive "$revision" -- atlas/muon | tar -x -C "$out/source"
    git -C "$out/source" apply --unidiff-zero --recount --check "$reviewed/M25.patch"
    git -C "$out/source" apply --unidiff-zero --recount "$reviewed/M25.patch"
    candidate_src="$out/source/atlas/muon"
    bash "$src/build-r01-stock-06.sh" "$out/candidate" "$sdk" "$fixtures" "$oracle" "$candidate_src"
    bash "$src/build-r01-stock-06.sh" "$out/instrumented" "$sdk" "$fixtures" "$oracle" "$candidate_src" -DATLAS_MUON_M01_STATS
    bash "$src/build-r01-stock-06.sh" "$out/control-instrumented" "$sdk" "$fixtures" "$oracle" \
        "$out/control-source/atlas/muon" -DATLAS_MUON_M01_STATS
    candidate="$out/candidate/provider-build/libatlas_muon.so"
    diagnostic="$out/instrumented/provider-build/libatlas_muon.so"
    control_instrumented="$out/control-instrumented/provider-build/libatlas_muon.so"
    nm -D "$control" "$candidate" > "$out/uninstrumented-symbols.txt"
    if rg -q atlas_muon_m01_rng_ "$out/uninstrumented-symbols.txt"; then exit 1; fi
    nm -D "$diagnostic" | rg atlas_muon_m01_rng_ > "$out/instrumented-exports.txt"
    test "$(wc -l < "$out/instrumented-exports.txt")" -eq 4
    python - "$out/release-draws.json" <<'PY'
import json
from pathlib import Path
import struct
import sys
import ROOT

seeds = {1001 + 2048 * lane for lane in range(5)} | {1002, 1001 | 0x80000000}
values = {}
for seed in sorted(seeds):
    random = ROOT.TRandom3()
    random.SetSeed(seed)
    values[str(seed)] = [struct.pack(">d", random.Gaus(0, 1)).hex() for _ in range(6)]
with Path(sys.argv[1]).open("x") as output:
    json.dump({"root_version": str(ROOT.gROOT.GetVersion()), "vectors": values}, output, indent=2)
    output.write("\n")
PY
    for variant in control candidate diagnostic; do
        opts=()
        if [[ "$variant" == diagnostic ]]; then opts+=(--instrumented); fi
        "${python[@]}" "$reviewed/probe_m25.py" edges "${!variant}" "$trace" "$candidate_src/momentum/contract.json" \
            "$out/release-draws.json" "$out/$variant-edges.json" "${opts[@]}"
    done
    "${python[@]}" "$reviewed/probe_m25.py" compare "$out/control-edges.json" "$out/candidate-edges.json" \
        "$out/diagnostic-edges.json" "$out/edges-comparison.json"
    for variant in control_instrumented diagnostic; do
        "${python[@]}" "$reviewed/probe_rng.py" campaign-counts "${!variant}" "$prod" "$src/columnar/run_muons.py" \
            "$INPUT" "$out/$variant-counts.npz" "$out/$variant-counts.json"
        cmp "$out/$variant-counts.npz" "$frozen"
        readelf -sW "${!variant}" | c++filt | rg 'rng_cache(\[|$|_next_way|_counts)' > "$out/$variant-tls-symbols.txt"
        readelf -lW "${!variant}" | rg TLS > "$out/$variant-tls-segment.txt"
    done
    for variant in control candidate; do
        "${python[@]}" "$src/audit_all_native_momentum.py" capture "${!variant}" "$trace" "$INPUT" \
            "$out/$variant-native-histogram.npz" "$out/$variant-all-lanes.json"
        cmp "$out/$variant-native-histogram.npz" "$frozen"
    done
    "${python[@]}" "$src/audit_all_native_momentum.py" compare "$out/control-all-lanes.json" \
        "$out/candidate-all-lanes.json" "$out/all-lane-comparison.json"
    bash "$src/validate-root-free-06.sh" "$out/full" "$contract" "$sdk" "$candidate" "$bridge"
    sha256sum "$reviewed/M25.patch" "$control" "$candidate" "$diagnostic" "$control_instrumented" "$out/"*.json \
        > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
