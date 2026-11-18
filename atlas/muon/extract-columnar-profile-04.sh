#!/usr/bin/env bash
set -eo pipefail
campaign=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
repo="$campaign/correctionlib"
source "$repo/tools/muon_reference/environment.sh"
out=${1:?completed profile path required}
sdk=${2:?pinned SDK venv required}
expected=${3:?accepted full histogram required}
entrypoint=${4:-$repo/atlas/muon/columnar/run_muons.py}
s="$out/sampled"
cp "$s/stderr.txt" "$s/workload.stderr"
cp "$s/self.txt" "$s/perf-self-report.txt"
cp "$s/stacks.txt" "$s/perf-call-report.txt"
cp "$s/build-ids.txt" "$s/perf-build-ids.txt"
perf script -i "$s/perf.data" --show-lost-events -F comm,pid,tid,cpu,time,event,ip,sym,dso > "$s/perf.script"
perf report --stdio --stats -i "$s/perf.data" > "$s/perf-event-stats.txt"
env -u PYTHONHOME -u PYTHONPATH "$sdk/bin/python" "$repo/atlas/muon/summarize_columnar_profile.py" \
    "$out" "$expected" "$entrypoint"
