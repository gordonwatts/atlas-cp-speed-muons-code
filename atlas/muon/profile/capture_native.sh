#!/usr/bin/env bash
set -euo pipefail

profile_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
profile_output=${1:?Usage: capture_native.sh NEW_DIR ACCEPTED_NPZ DEMO_SCRIPT N MIN_SECONDS [COLLECTOR_CPU] -- COMMAND}
profile_expected=$(realpath -e -- "${2:?Accepted histogram required}")
profile_entry=$(realpath -e -- "${3:?Visible script required}")
profile_passes=${4:?Pass count required}
profile_minimum=${5:?Minimum processing seconds required}
shift 5
profile_collector=18
if [[ ${1:-} != -- ]]; then
    profile_collector=${1:?Collector CPU or -- required}
    shift
fi
[[ ${1:-} == -- ]]
shift
[[ $# -gt 0 && "$profile_output" = /* && "$profile_passes" =~ ^[1-9][0-9]*$ ]]
[[ "$profile_collector" =~ ^[0-9]+$ && "$profile_minimum" =~ ^[0-9]+([.][0-9]+)?$ ]]
profile_template=("$@")
profile_python=${ATLAS_PROFILE_PYTHON:-python}
profile_timer=${ATLAS_PROFILE_TIMER:-/usr/bin/time}
if [[ ! -x "$profile_timer" ]]; then profile_timer="$profile_source/bash-profile-time.sh"; fi
[[ -x "$profile_timer" ]]
grep -Fxq '# /// script' "$profile_entry"

mkdir -- "$profile_output"
mkdir -- "$profile_output/control" "$profile_output/probe"
profile_probe="$profile_output/probe"
profile_started=$PWD
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export BLIS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 ROOT_MAX_THREADS=1
export LC_ALL=C
{
    printf 'Workload cwd: %s\n' "$profile_started"
    printf 'Analysis CPU: 18\nCollector / monitor CPU: %s\n' "$profile_collector"
    printf 'Timer: %s\n' "$profile_timer"
    printf 'Passes: %s\nMinimum pass processing seconds: %s\n' "$profile_passes" "$profile_minimum"
    /usr/bin/uname -srm
    if [[ -r /proc/sys/kernel/perf_event_paranoid ]]; then cat /proc/sys/kernel/perf_event_paranoid; fi
    printf 'Analysis affinity probe: '
    taskset -c 18 sh -c 'taskset -pc "$$"'
    printf 'Collector affinity probe: '
    taskset -c "$profile_collector" sh -c 'taskset -pc "$$"'
    printf 'Template: '
    printf '%q ' "${profile_template[@]}"
    printf '\n'
} > "$profile_output/configuration.txt"
sha256sum -- "$profile_entry" "$profile_expected" "$profile_source/"*.py "$profile_source/capture_native.sh" "$profile_timer" \
    > "$profile_output/input-sha256.txt"

profile_perf=${ATLAS_PROFILE_PERF:-perf}
profile_can_sample=0
if command -v "$profile_perf" > "$profile_probe/perf.path"; then
    "$profile_perf" --version > "$profile_probe/perf.version"
    "$profile_perf" list > "$profile_probe/perf-list.txt" 2> "$profile_probe/perf-list.stderr" || true
    for group in software hardware; do
        if [[ "$group" == software ]]; then events=cpu-clock:u; else events=cycles:u,instructions:u; fi
        status=0
        taskset -c 18 "$profile_perf" stat -x ';' -o "$profile_probe/$group.csv" -e "$events" -- /bin/true \
            > "$profile_probe/$group.stdout" 2> "$profile_probe/$group.stderr" || status=$?
        printf '%s\n' "$status" > "$profile_probe/$group.exit"
    done
    profile_probe_status=0
    "$profile_python" "$profile_source/profile_evidence.py" counter-support "$profile_probe" || profile_probe_status=$?
    if [[ $profile_probe_status == 0 ]]; then
        profile_can_sample=1
    elif [[ $profile_probe_status != 1 || ! -s "$profile_probe/counter-support.json" ]]; then
        printf 'Counter-probe evidence could not be interpreted. See %s\n' "$profile_probe" >&2
        exit "$profile_probe_status"
    fi
else
    printf 'perf executable unavailable: %s\n' "$profile_perf" > "$profile_probe/limitation.txt"
fi

profile_run() {
    local mode=$1
    local destination="$profile_output/$mode"
    local item ready_output=0 ready_passes=0
    local -a argv=() prefix=() command=()
    for item in "${profile_template[@]}"; do
        case "$item" in
            @OUTPUT@) argv+=("$destination/histogram.npz"); ready_output=1 ;;
            @REPEAT@) argv+=("$profile_passes"); ready_passes=1 ;;
            @RUNTIME@) argv+=("$destination/runtime.json") ;;
            *) argv+=("$item") ;;
        esac
    done
    if [[ $ready_output != 1 || $ready_passes != 1 ]]; then
        printf 'The command needs both @OUTPUT@ and @REPEAT@ argument tokens.\n' >&2
        return 2
    fi
    if [[ $mode == sampled ]]; then
        prefix=(taskset -c "$profile_collector" "$profile_timer" -f 'wall=%e user=%U system=%S exit=%x' -o "$destination/time.txt"
                "$profile_perf" record --sample-cpu -e cpu-clock:u -F 99 --call-graph dwarf -o "$destination/perf.data" --)
    else
        prefix=("$profile_timer" -f 'wall=%e user=%U system=%S exit=%x' -o "$destination/time.txt")
    fi
    command=("${prefix[@]}" taskset -c 18 nice -n 10 "${argv[@]}")
    printf '%q ' "${command[@]}" > "$destination/command.txt"
    printf '\n' >> "$destination/command.txt"
    (
        ulimit -f 262144
        exec "${command[@]}"
    ) > "$destination/workload.stdout" 2> "$destination/workload.stderr" &
    local root_pid=$!
    taskset -c "$profile_collector" "$profile_python" "$profile_source/profile_evidence.py" observe \
        "$root_pid" "$destination/processes.jsonl" &
    local observer_pid=$! result=0 observer_result=0
    wait "$root_pid" || result=$?
    wait "$observer_pid" || observer_result=$?
    printf '%s\n' "$result" > "$destination/exit"
    if [[ $observer_result != 0 ]]; then return "$observer_result"; fi
    return "$result"
}

if [[ $profile_can_sample == 0 && ${ATLAS_PROFILE_ALLOW_TARGETED:-0} != 1 ]]; then
    printf 'No usable software counter probe. Retained diagnostics in %s\n' "$profile_probe" >&2
    exit 1
fi
profile_run control
if [[ $profile_can_sample == 0 ]]; then
    "$profile_python" "$profile_source/profile_evidence.py" analyze "$profile_output" "$profile_expected" \
        "$profile_entry" "$profile_passes" --minimum-seconds "$profile_minimum" --collector-cpu "$profile_collector" \
        --targeted-only
    exit 0
fi

mkdir -- "$profile_output/sampled"
profile_capture_status=0
profile_run sampled || profile_capture_status=$?
profile_sample="$profile_output/sampled"
if [[ $profile_capture_status != 0 ]]; then
    if [[ ${ATLAS_PROFILE_ALLOW_TARGETED:-0} == 1 && ! -e "$profile_sample/histogram.npz" ]] &&
       grep -Ei 'perf_event_open.*(permission denied|operation not permitted|not supported)|No permission to enable.*cpu-clock|Access to performance monitoring.*limited' \
           "$profile_sample/workload.stderr" > "$profile_probe/sampling-denied.txt"; then
        "$profile_python" "$profile_source/profile_evidence.py" analyze "$profile_output" "$profile_expected" \
            "$profile_entry" "$profile_passes" --minimum-seconds "$profile_minimum" --collector-cpu "$profile_collector" \
            --targeted-only
        exit 0
    fi
    exit "$profile_capture_status"
fi
taskset -c "$profile_collector" "$profile_perf" script -i "$profile_sample/perf.data" \
    --show-lost-events -F comm,pid,tid,cpu,time,event,ip,sym,dso \
    > "$profile_sample/perf.script" 2> "$profile_sample/perf-script.stderr"
taskset -c "$profile_collector" "$profile_perf" report --stdio --no-children --call-graph none \
    --sort dso,symbol --show-nr-samples -i "$profile_sample/perf.data" \
    > "$profile_sample/perf-self-report.txt" 2> "$profile_sample/perf-self-report.stderr"
taskset -c "$profile_collector" "$profile_perf" report --stdio -i "$profile_sample/perf.data" \
    > "$profile_sample/perf-call-report.txt" 2> "$profile_sample/perf-call-report.stderr"
taskset -c "$profile_collector" "$profile_perf" report --stdio --stats -i "$profile_sample/perf.data" \
    > "$profile_sample/perf-event-stats.txt" 2> "$profile_sample/perf-event-stats.stderr" || true
taskset -c "$profile_collector" "$profile_perf" buildid-list -i "$profile_sample/perf.data" \
    > "$profile_sample/perf-build-ids.txt" 2> "$profile_sample/perf-build-ids.stderr"
"$profile_python" "$profile_source/profile_evidence.py" analyze "$profile_output" "$profile_expected" \
    "$profile_entry" "$profile_passes" --minimum-seconds "$profile_minimum" --collector-cpu "$profile_collector"
