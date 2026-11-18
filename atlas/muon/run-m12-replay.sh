#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh isolated diagnostic required}
source_dir=${2:?reviewed replay source required}
sdk="$campaign/results/checkpoint-06/stock-sdk-01/venv"
native="$repo/atlas/muon/momentum"
prod="$campaign/results/checkpoint-03/export-03/muon-production.json"
clean=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT -u PYTHONPATH -u PYTHONHOME
       -u CMAKE_PREFIX_PATH -u ROOTSYS -u ROOT_INCLUDE_PATH -u ATLAS_LOCAL_ROOT_BASE -u VIRTUAL_ENV PYTHONNOUSERSITE=1)
mkdir "$out"
{
    sha256sum "$source_dir/"* "$prod" "$native/momentum.cxx" "$native/momentum_payload.cxx" > "$out/inputs-sha256.txt"
    "${clean[@]}" "$sdk/bin/python" "$source_dir/replay_inputs.py" fixture "$INPUT" \
        "$repo/atlas/muon/columnar/run_muons.py" "$out/batches.tsv" "$out/fixture.json"
    "${clean[@]}" /usr/bin/cmake -S "$source_dir" -B "$out/build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++ -Dcorrectionlib_DIR="$sdk/lib/python3.14/site-packages/correctionlib/cmake" \
        -DMOMENTUM_SOURCE="$native" -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include"
    "${clean[@]}" /usr/bin/cmake --build "$out/build" --parallel 1
    "${clean[@]}" "$out/build/m12_named_map_replay" "$prod" "$out/batches.tsv" > "$out/pairs.tsv"
    "${clean[@]}" "$sdk/bin/python" "$source_dir/replay_inputs.py" summarize "$out/pairs.tsv" "$out/ranking.json"
} 2>&1 | tee "$out/job.log"
