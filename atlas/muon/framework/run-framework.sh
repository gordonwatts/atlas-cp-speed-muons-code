#!/usr/bin/env bash
set -eo pipefail

framework_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
worker_dir=$(cd -- "$framework_source/../../../tools/muon_reference" && pwd)
framework_library=$(realpath -- "${1:?Path to built libAtlasMuonFramework.so}")
framework_run=${2:?Absolute, fresh output directory required}
framework_mode=${3:?Run mode must be audit, acceptance or production}
framework_events=${4:-100}
[[ "$framework_run" = /* ]]
mkdir "$framework_run"
cd "$framework_run"

export ATLAS_MUON_FRAMEWORK_BRIDGE="$framework_library"
export MUON_MOMENTUM_SCHEME=AllSys
export MUON_ETA_SAGITTA=1
framework_input=${INPUT:?Source the campaign release environment for the frozen input}
framework_yaml="$worker_dir/smoke.yaml"
framework_events_option=(-e "$framework_events")
if [[ "$framework_events" == all ]]; then framework_events_option=(); fi
framework_run_command=(
    python "$framework_source/bridge_entry.py" --direct-driver
    -i "$framework_input" -t "$framework_yaml" -o reference
    "${framework_events_option[@]}"
)

for pathname in "$framework_library" "${ATLAS_MUON_PROVIDER:?}" "${ATLAS_MUON_MOMENTUM:?}" \
                "${ATLAS_MUON_SELECTION:?}" "${ATLAS_MUON_EFFICIENCY:?}"; do
    [[ "$pathname" = /* && -f "$pathname" ]]
    sha256sum -- "$pathname"
done > loaded-input-sha256.txt
sha256sum -- "$framework_yaml" "$worker_dir/run_reference.py" >> loaded-input-sha256.txt
printf '%q ' "${framework_run_command[@]}" > command.txt
printf '\n' >> command.txt

case "$framework_mode" in
    audit)
        export MUON_VALIDATION_OUTPUT=1
        unset MUON_REFERENCE_OUTPUT
        : "${MUON_STATUS_OBSERVER_LIBRARY:?Audit requires the unchanged reference status observer}"
        : "${ATLAS_MUON_FROZEN_CONFIG:?Audit requires the matching release full_config.json}"
        env -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT \
            ATLAS_MUON_DISPATCH_AUDIT=1 timeout --kill-after=5s 60s \
            "${framework_run_command[@]}" --dump-full-config > job.log 2>&1
        python "$framework_source/verify_dispatch.py" "$framework_run" "$framework_library" \
            "$ATLAS_MUON_FROZEN_CONFIG"
        ;;
    acceptance)
        export MUON_VALIDATION_OUTPUT=1
        unset MUON_REFERENCE_OUTPUT
        : "${MUON_STATUS_OBSERVER_LIBRARY:?Acceptance requires the unchanged reference status observer}"
        env -u ATLAS_MUON_DISPATCH_AUDIT -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT \
            "${framework_run_command[@]}" --dump-full-config > job.log 2>&1
        ;;
    production)
        export MUON_VALIDATION_OUTPUT=0
        export MUON_REFERENCE_OUTPUT=production
        unset MUON_STATUS_OBSERVER_LIBRARY
        { time \
            env -u ATLAS_MUON_DISPATCH_AUDIT -u LD_PRELOAD -u LD_DEBUG -u LD_DEBUG_OUTPUT \
            "${framework_run_command[@]}" > job.log 2>&1; } 2> time.txt
        ;;
    *)
        printf 'Unexpected framework mode %s\n' "$framework_mode" >&2
        exit 2
        ;;
esac
