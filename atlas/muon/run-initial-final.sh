#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh result root required}
install=${2:?validated final installed result required}
ref="$campaign/results/checkpoint-03"
revision=a54ceb3f1a30ba39b3cb3a819239575238789c1e
payload="$ref/export-03/muon-production.json"
installed="$install/pip-venv/lib/python3.14/site-packages/atlas_muon_portable"
cmp "$payload" "$installed/payload.json"
mkdir "$out" "$out/initial-source"
git -C "$repo" archive "$revision" -- atlas/muon/columnar | tar -x -C "$out/initial-source"
runner="$out/initial-source/atlas/muon/columnar/run_muons.py"
echo "2142b544bd454a2263c07a9ea2397832cae9852926bf458af827da55fb3a9cd0  $runner" | sha256sum -c -
python="$install/pip-venv/bin/python"
exec env -u PYTHONPATH -u PYTHONHOME "$python" "$repo/atlas/muon/benchmark_initial_final.py" \
    "$out/paired" "$INPUT" "$ref/c-acceptance-01/histogram.npz" "$ref/sdk-build-01/venv/bin/python" "$runner" \
    "$ref/provider-build-02/build/libatlas_muon.so" "$payload" "$python" \
    "$install/extracted/muon-standalone/bundle/muon_demo.py" "$installed/native/libatlas_muon.so" "$revision"
