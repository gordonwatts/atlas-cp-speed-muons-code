#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh B-only trial directory required}
contract=${2:?frozen acceptance required}
staged=${3:?reviewed B01 sources required}
revision=${4:?clean immediate control revision required}
control=${5:?accepted original bridge required}
provider=${6:?accepted unchanged provider required}
reusable_builds=${7:-}
stock="$campaign/results/checkpoint-06"
sdk="$stock/stock-sdk-01/venv"
reference="$campaign/results/checkpoint-02"
payload="$campaign/results/checkpoint-03/export-03/muon-production.json"
base="$campaign/results/checkpoint-07/m05-validation-02/full/B"
src="$repo/atlas/muon"
package="$sdk/lib/python3.14/site-packages/correctionlib"
export ATLAS_MUON_CORE_LIBRARY="$package/lib/libcorrectionlib.so"
export ATLAS_MUON_CORE_INCLUDE="$package/include"
export ATLAS_MUON_PROVIDER="$provider"
export ATLAS_MUON_MOMENTUM="$payload" ATLAS_MUON_SELECTION="$payload" ATLAS_MUON_EFFICIENCY="$payload"
export ATLAS_MUON_FROZEN_CONFIG="$campaign/results/checkpoint-13/final-b-dispatch-01/full_config.json"
export MUON_STATUS_OBSERVER_LIBRARY="$reference/status-build-04/libMuonEfficiencyStatusObserver.so"
python=(env -u PYTHONPATH -u PYTHONHOME "$sdk/bin/python")
mkdir "$out" "$out/source" "$out/fixture-source"
{
    git -C "$repo" archive "$revision" -- atlas/muon tools/muon_reference | tar -x -C "$out/source"
    # The accepted bridge inherits C++23 from root-config and links ColumnarCoreLib.
    sed 's/ -std=c++20 / /g' "$staged/B-alias-cache.patch" > "$out/stock-matched.patch"
    git -C "$out/source" apply --check "$out/stock-matched.patch"
    git -C "$out/source" apply "$out/stock-matched.patch"
    framework="$out/source/atlas/muon/framework"
    if [[ -z $reusable_builds ]]; then
        bash "$framework/build-framework.sh" "$out/candidate-build"
        ATLAS_MUON_B_ALIAS_PROBE=1 bash "$framework/build-framework.sh" "$out/diagnostic-build"
        reusable_builds=$out
    else
        cmp "$out/stock-matched.patch" "$reusable_builds/stock-matched.patch"
    fi
    candidate="$reusable_builds/candidate-build/libAtlasMuonFramework.so"
    diagnostic="$reusable_builds/diagnostic-build/libAtlasMuonFramework.so"
    nm -D "$control" "$candidate" > "$out/uninstrumented-symbols.txt"
    if rg -q atlas_muon_b_momentum_alias "$out/uninstrumented-symbols.txt"; then exit 1; fi
    nm -D "$diagnostic" | rg atlas_muon_b_momentum_alias > "$out/instrumented-exports.txt"
    # AnalysisBase's child component-name grammar disallows hyphens.
    sed 's/"muon-alias-native-control"/"muon_alias_native_control"/' "$staged/alias_fixture.cxx" \
        > "$out/fixture-source/alias_fixture.cxx"
    sed -e 's/ -std=c++20 / /g' -e 's/ -lColumnarCore / -lColumnarCoreLib /g' "$staged/build-alias-fixture.sh" \
        > "$out/fixture-source/build-alias-fixture.sh"
    bash "$out/fixture-source/build-alias-fixture.sh" "$out/fixture" "$diagnostic"
    (cd "$out/fixture"; ulimit -c 0; ATLAS_MUON_B_ALIAS_COUNTS=1 timeout --kill-after=5s 60s ./alias-fixture) \
        > "$out/fixture/job.log" 2>&1
    "${python[@]}" "$staged/check_alias_dispatch.py" "$out/fixture/job.log" "$out/fixture-counts.json"
    ATLAS_MUON_B_ALIAS_COUNTS=1 bash "$framework/run-framework.sh" "$diagnostic" "$out/smoke" audit 100
    "${python[@]}" "$staged/check_alias_dispatch.py" "$out/smoke/job.log" "$out/smoke-counts.json" --events 100
    ATLAS_MUON_B_ALIAS_COUNTS=1 bash "$framework/run-framework.sh" "$diagnostic" "$out/full-counts" production all
    "${python[@]}" "$staged/check_alias_dispatch.py" "$out/full-counts/job.log" "$out/full-counts.json" --events 20000
    bash "$src/run-b-full-03.sh" "$out/full" "$sdk" "$candidate" "$provider" "$payload" "$contract"
    "${python[@]}" "$repo/tools/muon_reference/compare_reference.py" "$base" "$out/full/reference.root" \
        "$base/histogram.npz" "$out/full/histogram.npz" "$contract" "$out/full/compare-control-B.json" \
        --candidate-systematics "$out/full/hist-reference.root"
    cmp "$out/full/histogram.npz" "$base/histogram.npz"
    sha256sum "$staged/"* "$out/stock-matched.patch" "$out/fixture-source/"* \
        "$control" "$candidate" "$diagnostic" "$provider" "$payload" "$out/"*.json > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
