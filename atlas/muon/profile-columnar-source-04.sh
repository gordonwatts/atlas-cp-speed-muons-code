#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?fresh profile result path required}
sdk=${2:?pinned SDK venv required}
provider=${3:?accepted uninstrumented provider required}
payload=${4:?accepted production payload required}
expected=${5:?accepted full histogram required}
mode=${6:-C}
bridge=${7:-}
mkdir "$out"
source_dir="$repo/atlas/muon"
python=(env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python")
if [[ "$mode" == B ]]; then
    [[ -f "$bridge" ]]
    framework=(env ATLAS_MUON_FRAMEWORK_BRIDGE="$bridge" ATLAS_MUON_PROVIDER="$provider"
        ATLAS_MUON_MOMENTUM="$payload" ATLAS_MUON_SELECTION="$payload" ATLAS_MUON_EFFICIENCY="$payload"
        MUON_MOMENTUM_SCHEME=AllSys MUON_ETA_SAGITTA=1 MUON_REFERENCE_OUTPUT=production MUON_VALIDATION_OUTPUT=0
        "$(command -v python)" "$source_dir/framework/bridge_entry.py"
        --direct-driver -i "$INPUT" -t "$repo/tools/muon_reference/smoke.yaml" -o reference -e 20000)
else
    [[ "$mode" == C ]]
fi
capability=unavailable
{
    date -u --iso-8601=seconds
    /usr/bin/uname -srm
    taskset -pc "$$"
    cat /proc/sys/kernel/perf_event_paranoid
    if command -v perf; then
        perf --version
        for counter in cpu-clock:u cycles:u,instructions:u; do
            status=0
            perf stat -e "$counter" -- /bin/true 2>&1 || status=$?
            echo "counter=$counter status=$status"
            if [[ "$counter" == cpu-clock:u && $status == 0 ]]; then capability=available; fi
        done
    fi
} > "$out/capability.txt" 2>&1

run() {
    local run=$1
    shift
    local directory="$out/$run"
    mkdir "$directory"
    local -a command=("$@")
    if [[ "$mode" == B ]]; then
        command+=("${framework[@]}")
    else
        command+=("${python[@]}" "$source_dir/columnar/run_muons.py" histogram
            "$INPUT" "$directory/histogram.npz" --mode full --provider "$provider"
            --momentum "$payload" --selection "$payload" --efficiency "$payload" --chunk-size 2000)
    fi
    printf '%q ' "${command[@]}" > "$directory/command.txt"
    local status=0 monitor_status=0
    (
        ulimit -f 131072
        cd "$directory"
        TIMEFORMAT='wall=%3R user=%3U system=%3S'
        { time "${command[@]}" > "$directory/stdout.txt" 2> "$directory/stderr.txt"; } 2> "$directory/time.txt"
    ) &
    local owner=$!
    "${python[@]}" "$source_dir/profile/profile_evidence.py" observe "$owner" "$directory/processes.jsonl" &
    local monitor=$!
    wait "$owner" || status=$?
    wait "$monitor" || monitor_status=$?
    printf 'command=%d\nmonitor=%d\n' "$status" "$monitor_status" > "$directory/status.txt"
    if [[ $status != 0 ]]; then return "$status"; fi
    [[ $monitor_status == 0 ]] || return "$monitor_status"
    if [[ "$mode" == B ]]; then
        TIMEFORMAT='wall=%3R user=%3U system=%3S'
        { time "${python[@]}" "$repo/tools/muon_reference/reference_histogram.py" \
            "$directory/reference.root" "$directory/hist-reference.root" "$directory/histogram.npz" \
            > "$directory/histogram.stdout" 2> "$directory/histogram.stderr"; } 2> "$directory/histogram-time.txt"
    fi
    cmp "$directory/histogram.npz" "$expected"
}

run control
if [[ "$capability" == available ]]; then
    sample="$out/sampled"
    if run sampled timeout --kill-after=5s 105s perf record --sample-cpu -e cpu-clock:u -F 99 --call-graph dwarf -o "$sample/perf.data" --; then
        perf report --stdio --no-children --sort dso,symbol --show-nr-samples -i "$sample/perf.data" > "$sample/self.txt"
        perf report --stdio --sort dso,symbol --show-nr-samples -i "$sample/perf.data" > "$sample/stacks.txt"
        perf buildid-list -i "$sample/perf.data" > "$sample/build-ids.txt"
        sha256sum "$expected" "$out/control/histogram.npz" "$sample/histogram.npz" > "$out/output-sha256.txt"
    else
        echo "Native sample collection failed; raw status and error retained" > "$out/limitation.txt"
    fi
else
    echo "Native software clock unavailable; no sample shares produced" > "$out/limitation.txt"
fi
if [[ "$mode" == C ]]; then
    bash "$source_dir/run-c-phases-03.sh" "$out/phases" "$sdk" "$provider" "$payload" "$expected"
fi
