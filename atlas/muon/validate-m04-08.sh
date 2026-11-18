#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh M04 trial directory required}
contract=${2:?frozen acceptance required}
reviewed=${3:?reviewed M04 materials directory required}
revision=${4:?clean immediate control revision required}
baseline=${5:?accepted immediate control binary required}
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
mkdir "$out" "$out/source"
{
    git -C "$repo" archive "$revision" -- atlas/muon | tar -x -C "$out/source"
    git -C "$out/source" apply --check "$reviewed/M04.patch"
    git -C "$out/source" apply "$reviewed/M04.patch"
    candidate_src="$out/source/atlas/muon"
    bash "$src/build-r01-stock-06.sh" "$out/candidate" "$sdk" "$fixtures" "$oracle" "$candidate_src"
    bash "$src/build-r01-stock-06.sh" "$out/instrumented" "$sdk" "$fixtures" "$oracle" "$candidate_src" -DATLAS_MUON_M04_STATS
    candidate="$out/candidate/provider-build/libatlas_muon.so"
    diagnostic="$out/instrumented/provider-build/libatlas_muon.so"
    nm -D "$baseline" "$candidate" > "$out/uninstrumented-symbols.txt"
    if rg -q atlas_muon_m04_ "$out/uninstrumented-symbols.txt"; then exit 1; fi
    nm -D "$diagnostic" | rg atlas_muon_m04_ > "$out/instrumented-exports.txt"
    test "$(wc -l < "$out/instrumented-exports.txt")" -eq 2
    sdk_config="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
    "${clean[@]}" /usr/bin/cmake -S "$reviewed" -B "$out/layout-fixture" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -Dcorrectionlib_DIR="$sdk_config" \
        -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include" -DMUON_MOMENTUM_SOURCE="$candidate_src/momentum"
    "${clean[@]}" /usr/bin/cmake --build "$out/layout-fixture" --parallel 1
    "${clean[@]}" "$out/layout-fixture/m04_layout_probe" > "$out/layout-fixture.txt"
    for variant in baseline candidate diagnostic; do
        opts=()
        if [[ "$variant" == diagnostic ]]; then opts+=(--instrumented); fi
        "${python[@]}" "$reviewed/probe_outputs.py" edges "${!variant}" "$trace" "$candidate_src/momentum/contract.json" \
            "$fixtures/axes.txt" "$out/edges-$variant.json" "${opts[@]}"
    done
    "${python[@]}" "$reviewed/probe_outputs.py" compare \
        "$out/edges-baseline.json" "$out/edges-candidate.json" "$out/edges-diagnostic.json" "$out/edges-comparison.json"
    for variant in baseline candidate; do
        "${python[@]}" "$src/audit_all_native_momentum.py" capture "${!variant}" "$trace" "$INPUT" \
           "$out/$variant-native-histogram.npz" "$out/$variant-all-lanes.json"
        cmp "$out/$variant-native-histogram.npz" "$frozen"
    done
    "${python[@]}" "$src/audit_all_native_momentum.py" compare "$out/baseline-all-lanes.json" \
           "$out/candidate-all-lanes.json" "$out/all-lane-comparison.json"
    "${python[@]}" "$reviewed/probe_m04_counts.py" "$diagnostic" "$prod" "$src/columnar/run_muons.py" "$INPUT" \
        "$out/count-histogram.npz" "$out/bin-counts.json"
    cmp "$out/count-histogram.npz" "$frozen"
    bash "$src/validate-root-free-06.sh" "$out/full" "$contract" "$sdk" "$candidate" "$bridge"
    sha256sum "$reviewed/M04.patch" "$baseline" "$candidate" "$diagnostic" "$out/"*.json > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
