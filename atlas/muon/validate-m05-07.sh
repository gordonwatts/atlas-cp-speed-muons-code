#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh M05 trial directory required}
contract=${2:?frozen acceptance contract required}
reviewed=${3:?reviewed trial materials directory required}
stock="$campaign/results/checkpoint-06"
ref="$campaign/results/checkpoint-03"
sdk="$stock/stock-sdk-01/venv"
control="$stock/r01-stock-provider-03/provider-build/libatlas_muon.so"
bridge="$stock/r01-stock-bridge-01/libAtlasMuonFramework.so"
oracle="$stock/r01-gcc15-build-01/root.tsv"
fixtures="$stock/parity-data-01"
prod="$ref/export-03/muon-production.json"
trace="$ref/export-03/muon-diagnostic.json"
frozen="$ref/c-acceptance-01/histogram.npz"
src="$repo/atlas/muon"
driver="$src/columnar/run_muons.py"
clean=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME -u CMAKE_PREFIX_PATH
       -u ROOTSYS -u ROOT_INCLUDE_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
python=("${clean[@]}" "$sdk/bin/python")
mkdir "$out" "$out/source"
{
    git -C "$repo" archive f376dd0d6388ec79342615f03545d25df1eab475 -- atlas/muon | tar -x -C "$out/source"
    git -C "$out/source" apply --check "$reviewed/M05-R01-on-M11.patch"
    git -C "$out/source" apply "$reviewed/M05-R01-on-M11.patch"
    candidate_src="$out/source/atlas/muon"
    cp "$reviewed/stat_combination_cache.inc" "$candidate_src/momentum/"
    bash "$src/build-r01-stock-06.sh" "$out/candidate" "$sdk" "$fixtures" "$oracle" "$candidate_src"
    bash "$src/build-r01-stock-06.sh" "$out/instrumented" "$sdk" "$fixtures" "$oracle" "$candidate_src" -DATLAS_MUON_M05_STATS
    candidate="$out/candidate/provider-build/libatlas_muon.so"
    instrumented="$out/instrumented/provider-build/libatlas_muon.so"
    nm -D "$control" "$candidate" > "$out/uninstrumented-symbols.txt"
    if rg -q atlas_muon_m05_covariance_ "$out/uninstrumented-symbols.txt"; then
        echo "M05 instrumentation present in an ordinary candidate" >&2
        exit 1
    fi
    nm -D "$instrumented" | rg atlas_muon_m05_covariance_ > "$out/instrumented-exports.txt"
    test "$(wc -l < "$out/instrumented-exports.txt")" -eq 3
    externals="${AtlasArea/AnalysisBase\//AnalysisBaseExternals\/}/InstallArea/$CMTCONFIG"
    eigen=$(dirname "$(find "$externals" -iname Eigen3Config.cmake -print -quit)")
    sdk_config="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
    "${clean[@]}" /usr/bin/cmake -S "$reviewed" -B "$out/cache-fixture" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -Dcorrectionlib_DIR="$sdk_config" -DEigen3_DIR="$eigen" \
        -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include" -DMOMENTUM_SOURCE="$candidate_src/momentum"
    "${clean[@]}" /usr/bin/cmake --build "$out/cache-fixture" --parallel 1
    "${clean[@]}" "$out/cache-fixture/m05_fixture" > "$out/cache-fixture.txt"
    stat_probe="$src/momentum/matrix_fixture/probe_matrix.py"
    for variant in control candidate; do
        "${python[@]}" "$stat_probe" capture "${!variant}" "$prod" "$trace" \
            "$candidate_src/momentum/contract.json" "$out/$variant-batch.json"
        "${python[@]}" "$src/audit_all_native_momentum.py" capture "${!variant}" "$trace" "$INPUT" \
            "$out/$variant-native-histogram.npz" "$out/$variant-all-lanes.json"
        cmp "$out/$variant-native-histogram.npz" "$frozen"
    done
    "${python[@]}" "$stat_probe" compare "$out/control-batch.json" "$out/candidate-batch.json" "$out/batch-comparison.json"
    "${python[@]}" "$src/audit_all_native_momentum.py" compare "$out/control-all-lanes.json" \
        "$out/candidate-all-lanes.json" "$out/all-lane-comparison.json"
    "${python[@]}" "$reviewed/probe_cache.py" "$instrumented" "$prod" "$driver" "$INPUT" \
        "$out/counter-histogram.npz" "$out/cache-counts.json"
    cmp "$out/counter-histogram.npz" "$frozen"
    "${python[@]}" - "$out/all-lane-comparison.json" "$out/cache-counts.json" <<'PY'
import json
from pathlib import Path
import sys
calls, counts = [json.loads(Path(name).read_text()) for name in sys.argv[1:]]
assert calls["stat_calls"] == counts["phases"]["nominal"]["requests"]
assert counts["phases"]["shifted"]["requests"] == 0
print("Matched the independent native stat call count", calls["stat_calls"])
PY
    bash "$src/validate-root-free-06.sh" "$out/full" "$contract" "$sdk" "$candidate" "$bridge"
    sha256sum "$reviewed/M05-R01-on-M11.patch" "$reviewed/stat_combination_cache.inc" "$candidate" "$instrumented" \
        "$out/batch-comparison.json" "$out/all-lane-comparison.json" "$out/cache-counts.json" \
        > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
