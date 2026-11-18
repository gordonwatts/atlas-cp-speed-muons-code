#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
result=${1:?existing checkpoint result path required}
sdk=${2:?pinned SDK venv required}
contract=${3:?frozen acceptance path required}
out="$result/diagnostics-01"
mkdir "$out"
ref="$campaign/results/checkpoint-02"
a="$ref/full-observer-01"
b="$result/b-acceptance-01"
c="$result/c-acceptance-01"
scripts="$repo/tools/muon_reference"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
run_plot() {
    local label=$1 run=$2 candidate=$3
    shift 3
    "${python[@]}" "$scripts/figures/extract_plot_data.py" \
        --reference "$a/reference.root" --reference-systematics "$a/hist-reference.root" \
        --reference-audit "$ref/full-observer-audit.json" --reference-histogram "$a/histogram.npz" \
        --candidate "$candidate" --candidate-histogram "$run/histogram.npz" --comparison "$run/compare.json" \
        --acceptance "$contract" --path-label "$label" --output "$out/$label-plot-data.json.gz" "$@"
}
{
    provider="$result/provider-build-02/build/libatlas_muon.so"
    "${python[@]}" "$scripts/diagnose_c_seed_domain.py" "$c/observations.root" "$out/original-seed-domain.json"
    "${python[@]}" "$repo/atlas/muon/probe_bundle_startup.py" "$provider" "$result/export-03/muon-production.json" "$out/correction-set-load.json"
    run_plot C "$c" "$c/observations.root" --implementation-evidence "$result/export-03/sha256.txt"
    run_plot B "$b" "$b/reference.root" --candidate-systematics "$b/hist-reference.root" \
        --implementation-evidence "$result/b-smoke-02/binding-map.json" --implementation-evidence "$result/export-03/sha256.txt"
    sha256sum "$a/reference.root" "$a/hist-reference.root" "$a/histogram.npz" \
      "$b/reference.root" "$b/hist-reference.root" "$b/histogram.npz" "$b/compare.json" \
      "$c/observations.root" "$c/histogram.npz" "$c/compare.json" "$out/"*.json "$out/"*.json.gz \
      "$result/export-03/muon-production.json" "$provider" "$result/framework-build-05/libAtlasMuonFramework.so" \
      > "$out/artifact-sha256.txt"
} 2>&1 | tee "$out/job.log"
