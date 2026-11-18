#!/usr/bin/env bash
set -euo pipefail
capture=${1:?recorded capture parent required}
out=${2:?fresh output required}
mkdir "$out"
lib=$(awk '$2 ~ /\/libatlas_muon.so$/ {print $2}' "$capture/sampled/perf-build-ids.txt")
[[ -f $lib ]]
awk '$2 ~ /\/libatlas_muon.so$/' "$capture/sampled/perf-build-ids.txt" > "$out/provider-build-id.txt"
sha256sum "$lib" > "$out/provider-sha256.txt"
perf script -i "$capture/sampled/perf.data" -F comm,pid,tid,cpu,time,event,ip,sym,symoff,dso,dsoff \
    | awk '
        /^python / { leaf = ""; first = 1; next }
        /^[[:space:]]+[[:xdigit:]]+[[:space:]]/ {
            if (leaf && /MomentumNode::evaluate_impl/ && /libatlas_muon.so/) print leaf "\t" $0;
            leaf = first && ($2 ~ /memcmp|bcmp/) ? $2 : "";
            first = 0;
        }
        /^$/ { leaf = ""; first = 0 }
      ' > "$out/direct-memcmp-parents.txt"
[[ $(wc -l < "$out/direct-memcmp-parents.txt") == 71 ]]
awk '{sub(/\+0x[[:xdigit:]]+$/, "", $1); print $1, $2, $NF}' "$out/direct-memcmp-parents.txt" \
    | sed -E 's| \([^ ]*/| (|' | sort | uniq -c > "$out/direct-parent-address-counts.txt"
objdump -d -C "$lib" > "$out/provider-disassembly.txt"
sed -E 's/.*\+0x([[:xdigit:]]+)\)$/\1/' "$out/direct-parent-address-counts.txt" | sort -u | while read -r offset; do
    printf '\nELF offset 0x%s\n' "$offset"
    awk -v wanted="0x$offset" '
        /^[[:space:]]+[[:xdigit:]]+:/ {
            address = $1;
            sub(/:$/, "", address);
            if (!found && strtonum("0x" address) > strtonum(wanted)) {
                for (m = NR - 10; m < NR; ++m) print old[m % 10];
                found = 1;
            }
            if (found && shown++ < 5) print;
            if (shown == 5) exit;
        }
        { old[NR % 10] = $0 }
      ' "$out/provider-disassembly.txt"
done > "$out/producer-callsite-disassembly.txt"
