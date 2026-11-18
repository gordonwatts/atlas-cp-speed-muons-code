#!/usr/bin/env bash

set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
smoke_id=${1:?fresh test label required}
export MUON_MOMENTUM_SCHEME=${2:?momentum scheme required}
export MUON_ETA_SAGITTA=${3:?eta split 0 or 1 required}
export MUON_VALIDATION_OUTPUT=${4:-0}
mkdir "$RESULTS/$smoke_id"
cd "$RESULTS/$smoke_id"
printf 'MUON_MOMENTUM_SCHEME=%s MUON_ETA_SAGITTA=%s MUON_VALIDATION_OUTPUT=%s\n' "$MUON_MOMENTUM_SCHEME" "$MUON_ETA_SAGITTA" "$MUON_VALIDATION_OUTPUT" > command.txt
printf '%q ' python "$reference_dir/run_reference.py" --direct-driver --dump-full-config -i "$INPUT" -t "$reference_dir/smoke.yaml" -o reference -e 100 >> command.txt
printf '\n' >> command.txt
python "$reference_dir/run_reference.py" --direct-driver --dump-full-config -i "$INPUT" -t "$reference_dir/smoke.yaml" -o reference -e 100 > job.log 2>&1 &
worker=$!
touch loaded-libraries-samples.txt
{
    printf 'pid comm LinuxThreads nice CPU\n'
    while kill -0 "$worker" 2>/dev/null; do
        ps -o pid=,comm=,nlwp=,ni=,psr= -p "$worker" || break
        taskset -pc "$worker" 2>/dev/null || break
        awk '/^Threads:|^Cpus_allowed_list:/' "/proc/$worker/status" 2>/dev/null || true
        awk '{print $NF}' "/proc/$worker/maps" 2>/dev/null | rg '/lib(Muon|Systematics|Asg|Core|Imt|tbb|gomp|omp|Roo|ROOT)' | sort -u >> loaded-libraries-samples.txt || true
        sleep 0.2
    done
} > process-samples.txt
wait "$worker" || { status=$?; tail -75 job.log; exit "$status"; }
sort -u loaded-libraries-samples.txt > loaded-libraries.txt
tail -60 job.log
