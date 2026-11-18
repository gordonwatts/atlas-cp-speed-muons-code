#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?existing checkpoint result path required}
sdk=${2:?pinned SDK venv required}
out="$result/boundary-checks-01"
mkdir "$out"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
src="$repo/atlas/muon"
provider="$result/provider-build-02/build/libatlas_muon.so"
prod="$result/export-03/muon-production.json"
{
    python - "$out/release-gaussians.json" <<'PY'
import json
from pathlib import Path
import sys
import ROOT

seed = 1 + 125000 + 1250 + 314159
random = ROOT.TRandom3()
random.SetSeed(seed)
with Path(sys.argv[1]).open("x") as output:
    json.dump({"root_version": str(ROOT.gROOT.GetVersion()), "seed": seed,
               "gaussians": [random.Gaus(0, 1) for _ in range(6)]}, output, indent=2)
    output.write("\n")
PY
    "${python[@]}" "$src/momentum/probe_momentum.py" --provider "$provider" \
        --payload "$result/export-03/momentum-production.json" \
        --trace-payload "$result/export-03/momentum-diagnostic.json" \
        --release-rng-fixture "$out/release-gaussians.json" --runtime-provenance "$out/momentum-runtime.json"
    driver="$src/columnar/run_muons.py"
    c_probe="$src/columnar/probe_columnar.py"
    opts=(--provider "$provider" --momentum "$prod" --selection "$prod" --efficiency "$prod" --mode full)
    "${python[@]}" "$driver" histogram "$INPUT" "$out/chunk-173.npz" "${opts[@]}" --chunk-size 173
    "${python[@]}" "$c_probe" histograms "$out/chunk-173.npz" "$result/c-acceptance-01/histogram.npz"
    if cmp -s "$out/chunk-173.npz" "$result/c-acceptance-01/histogram.npz"; then
        echo "Chunk 173 and the accepted full histogram also have identical bytes"
    fi
    "${python[@]}" "$driver" histogram "$INPUT" "$out/no-events.npz" "${opts[@]}" --stop 0
    "${python[@]}" - "$out/no-events.npz" <<'PY'
import sys
import numpy as np

with np.load(sys.argv[1], allow_pickle=False) as payload:
    assert payload["states"].shape == (97,)
    for field in ("count", "sumw", "sumw2"):
        assert payload[field].shape == (102, 97)
        assert np.count_nonzero(payload[field]) == 0
print("Accepted 97 zero-filled categories and all flow rows for zero events")
PY
    stored_a="$campaign/results/checkpoint-02/production-series-01/block-02-stored/histogram.npz"
    stored_b="$result/b-production-series-02/block-02-stored/histogram.npz"
    cmp "$stored_a" "$stored_b"
    echo "A and B stored controls have identical histogram bytes"
    sha256sum "$stored_a" "$stored_b"
    sha256sum "$out/"*.json "$out/"*.npz
} 2>&1 | tee "$out/job.log"
