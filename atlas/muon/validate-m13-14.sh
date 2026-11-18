#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh M13 trial directory required}
contract=${2:?frozen acceptance required}
reviewed=${3:?reviewed M13 sources required}
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
mkdir "$out" "$out/source"
{
    git -C "$repo" archive "$revision" -- atlas/muon | tar -x -C "$out/source"
    git -C "$out/source" apply --check "$reviewed/M13.patch"
    git -C "$out/source" apply "$reviewed/M13.patch"
    candidate_src="$out/source/atlas/muon"
    bash "$src/build-r01-stock-06.sh" "$out/candidate" "$sdk" "$fixtures" "$oracle" "$candidate_src"
    bash "$src/build-r01-stock-06.sh" "$out/instrumented" "$sdk" "$fixtures" "$oracle" "$candidate_src" -DATLAS_MUON_M13_STATS
    candidate="$out/candidate/provider-build/libatlas_muon.so"
    diagnostic="$out/instrumented/provider-build/libatlas_muon.so"
    nm -D "$control" "$candidate" > "$out/uninstrumented-symbols.txt"
    if rg -q atlas_muon_m13_ "$out/uninstrumented-symbols.txt"; then exit 1; fi
    nm -D "$diagnostic" | rg atlas_muon_m13_ > "$out/instrumented-exports.txt"
    test "$(wc -l < "$out/instrumented-exports.txt")" -eq 2
    "${clean[@]}" /usr/bin/cmake -S "$reviewed" -B "$out/fenv-build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -Dcorrectionlib_DIR="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
    "${clean[@]}" /usr/bin/cmake --build "$out/fenv-build" --parallel 1
    probe="$out/fenv-build/m13_fenv_probe"
    "${python[@]}" "$reviewed/probe_counts.py" unproved-map "$prod" "$out/unproved-map.json"
    for variant in control candidate diagnostic; do
        opts=()
        if [[ $variant == diagnostic ]]; then opts+=(counts); fi
        "${clean[@]}" "$probe" "${!variant}" "$prod" "$trace" "${opts[@]}" \
            > "$out/$variant-fenv.tsv" 2> "$out/$variant-fenv-counts.tsv"
        "${clean[@]}" "$probe" "${!variant}" "$out/unproved-map.json" "$trace" "${opts[@]}" \
            > "$out/$variant-unproved-map.tsv" 2> "$out/$variant-unproved-map-counts.tsv"
        cmp "$out/control-fenv.tsv" "$out/$variant-fenv.tsv"
        cmp "$out/control-unproved-map.tsv" "$out/$variant-unproved-map.tsv"
    done
    "${python[@]}" "$reviewed/probe_counts.py" campaign "$diagnostic" "$prod" "$src/columnar/run_muons.py" \
        "$INPUT" "$out/campaign-counts.npz" "$out/campaign-counts.json"
    cmp "$out/campaign-counts.npz" "$frozen"
    for width in 12 40; do
        native_payload=$prod
        opts=()
        if [[ $width == 40 ]]; then native_payload=$trace; opts+=(--trace); fi
        for variant in control candidate; do
            output="$out/$variant-width-$width"
            "${python[@]}" "$reviewed/probe_counts.py" lanes "${!variant}" "$native_payload" "$src/columnar/run_muons.py" \
                "$INPUT" "$output.npz" "$output.json" "${opts[@]}"
            cmp "$output.npz" "$frozen"
        done
        "${python[@]}" "$reviewed/probe_counts.py" compare "$out/control-width-$width.json" \
            "$out/candidate-width-$width.json" "$out/width-$width-comparison.json"
    done
    bash "$src/validate-root-free-06.sh" "$out/full" "$contract" "$sdk" "$candidate" "$bridge"
    sha256sum "$reviewed/M13.patch" "$reviewed/CMakeLists.txt" "$reviewed/probe_fenv.cxx" "$reviewed/probe_counts.py" \
        "$control" "$candidate" "$diagnostic" "$prod" "$trace" "$out/"*.json "$out/"*.tsv > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
