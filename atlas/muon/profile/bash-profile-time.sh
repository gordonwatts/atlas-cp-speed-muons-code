#!/usr/bin/env bash
set -u
if [[ ${1:-} != -f || ${2:-} != 'wall=%e user=%U system=%S exit=%x' || ${3:-} != -o || $# -lt 5 ]]; then
    echo "Expected the profile timer format, output file and a command" >&2
    exit 2
fi
output=$4
shift 4
export LC_ALL=C
TIMEFORMAT='wall=%3R user=%3U system=%3S'
exec 3>&1 4>&2
elapsed=$({ time "$@" >&3 2>&4; } 2>&1)
status=$?
printf '%s exit=%d\n' "$elapsed" "$status" > "$output"
exit "$status"
