#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?existing M11 validation path required}
reference="$campaign/results/checkpoint-03"
probe="$repo/atlas/muon/momentum/matrix_fixture/probe_matrix.py"
contract="$repo/atlas/muon/momentum/contract.json"
control="$campaign/results/checkpoint-04/m01-candidate-build-01/build/libatlas_muon.so"
candidate="$out/candidate-build/build/libatlas_muon.so"
python=(env -u PYTHONHOME -u PYTHONPATH "$reference/sdk-build-01/venv/bin/python")
for variant in control candidate; do
    "${python[@]}" "$probe" capture "${!variant}" "$reference/export-03/muon-production.json" \
        "$reference/export-03/muon-diagnostic.json" "$contract" "$out/$variant-batch.json"
done
"${python[@]}" "$probe" compare "$out/control-batch.json" "$out/candidate-batch.json" "$out/batch-comparison.json"
