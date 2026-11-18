#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh trial output path required}
contract=${2:?frozen acceptance contract required}
patch_file=${3:?reviewed M11 patch required}
reference="$campaign/results/checkpoint-03"
accepted="$campaign/results/checkpoint-04"
sdk="$reference/sdk-build-01/venv"
src="$repo/atlas/muon"
fixture="$src/momentum/matrix_fixture"
python=(env -u PYTHONPATH -u PYTHONHOME "$sdk/bin/python")
control_commit=8864a5ca57593d72620d09ea9f944721dc9a3d6b
prod="$reference/export-03/muon-production.json"
trace="$reference/export-03/muon-diagnostic.json"
frozen="$reference/c-acceptance-01/histogram.npz"
mkdir "$out"
{
    mkdir "$out/control-source" "$out/candidate-source"
    git -C "$repo" archive "$control_commit" -- atlas/muon | tar -x -C "$out/control-source"
    git -C "$repo" archive "$control_commit" -- atlas/muon | tar -x -C "$out/candidate-source"
    git -C "$out/candidate-source" apply --check "$patch_file"
    git -C "$out/candidate-source" apply "$patch_file"
    candidate_src="$out/candidate-source/atlas/muon"
    bash "$src/build-provider-03.sh" "$out/candidate-build" "$sdk" "$candidate_src"
    candidate="$out/candidate-build/build/libatlas_muon.so"
    externals="${AtlasArea/AnalysisBase\//AnalysisBaseExternals\/}/InstallArea/$CMTCONFIG"
    eigen=$(dirname "$(find "$externals" -iname Eigen3Config.cmake -print -quit)")
    root=$(dirname "$(find "$externals" -iname ROOTConfig.cmake -print -quit)")
    sdk_config="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
    for variant in control candidate; do
        variant_src="$out/$variant-source/atlas/muon/momentum"
        option=OFF
        [[ "$variant" != candidate ]] || option=ON
        cmake -S "$fixture" -B "$out/$variant-fixture" -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_CXX_COMPILER="$(command -v g++)" -Dcorrectionlib_DIR="$sdk_config" \
            -DROOT_DIR="$root" -DEigen3_DIR="$eigen" -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include" \
            -DMOMENTUM_SOURCE="$variant_src" -DM11_CANDIDATE="$option"
        cmake --build "$out/$variant-fixture" --parallel 1
        "$out/$variant-fixture/m11_fixture" > "$out/$variant-fixture.tsv"
    done
    cmp "$out/control-fixture.tsv" "$out/candidate-fixture.tsv"
    echo "M11 structural matrix checks are bitwise identical"
    bash "$src/run-c-full-03.sh" "$out/C" "$sdk" "$candidate" "$trace" "$contract"
    bash "$src/run-b-full-03.sh" "$out/B" "$sdk" "$reference/framework-build-05/libAtlasMuonFramework.so" "$candidate" "$prod" "$contract"
    "${python[@]}" "$src/momentum/compare_release_momentum_trace.py" \
        "$reference/release-momentum-trace-01/trace.tsv" "$out/C/observations.root" "$out/release-primitives.json"
    cmp "$out/C/histogram.npz" "$out/B/histogram.npz"
    cmp "$out/C/histogram.npz" "$frozen"
    opts=(--mode full --provider "$candidate" --momentum "$prod" --selection "$prod" --efficiency "$prod")
    driver="$src/columnar/run_muons.py"
    "${python[@]}" "$driver" histogram "$INPUT" "$out/chunk-173.npz" "${opts[@]}" --chunk-size 173
    cmp "$out/chunk-173.npz" "$frozen"
    "${python[@]}" "$driver" histogram "$INPUT" "$out/no-events.npz" "${opts[@]}" --stop 0
    cmp "$out/no-events.npz" "$accepted/m01-validation-01/no-events.npz"
    sha256sum "$patch_file" "$candidate" "$prod" "$trace" "$out/"*.{json,npz,tsv} \
        "$out/C/compare.json" "$out/B/compare.json" "$out/C/histogram.npz" "$out/B/histogram.npz" \
        > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
