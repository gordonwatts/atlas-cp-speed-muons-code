# M13 conditional diagnostic resolution work

`M13.patch` targets the adopted R01/M01/M11/M05 stock provider at the checkpoint-13 delivery source `92be0fc35363c37a3a7a21cdf4d0c6bb2ece6623`. It conditionally omits exactly the two smearing-inclusive resolution calculations in a production momentum evaluation. All loaded correction names, argument order, type conversions, provider symbols, payload bytes and C/B caller paths retain their existing contracts. The patch uses the established R01 `Axis` and loaded `PeriodMaps` types and does not depend on any reader, histogram, or axis-sharing candidate.

## Read and write dependencies

`expected_resolution(..., false)` supplies the expected percentage resolutions for scale/smear recombination. Both calls and the recombination remain in the original sequence. In particular, recombination has data-dependent divisions, and a discarded result does not justify dropping its poles or floating-point exceptions. The two `expected_resolution(..., true) * CB.pt` results feed the 40-lane diagnostic outputs and the RHO-center calculation. RHO always executes the original calculations even at the frozen fixed rho. Both diagnostic widths retain the full statistical covariance path and the exact `rho * cb_direct + (1 - rho) * cb_stat` arithmetic, including NaN propagation when rho is one. Gaussian draws, seed, scale/smear corrections, charge, four-vector energy, raw and corrected detector momenta, status and write mask follow the existing implementation.

The 12-lane contract remains `called`, `status`, `write_mask`, `pt`, `eta`, `phi`, `energy`, `charge`, `id_pt`, `me_pt`, `id_charge`, `me_charge`. It still evaluates by the original scalar and batch CorrectionLib entry points with the same 80 input columns. The full 40-lane payload always runs the numerical trace, including the four expected-resolution quantities, the recombined scale/smear number, statistical status and RHO diagnostics. Refer to the unchanged `atlas/muon/momentum/contract.json` for exact positional names and storage semantics. No analysis script, wheel API or external raw-input record needs changing.

## Floating-point guard

The guard is conservative. At construction, a 12-lane node checks the immutable period's six expected and five participating nominal histograms. The R01 loader already validates content sizes and strictly increasing finite axis edges. M13 also requires zero or bounded normal binary64 map data and axes, using integer magnitude words so a NaN or denormal cannot itself alter the environment during classification. A payload with even one unproved cell or layout in those maps uses the original calculation for the entire period. The loader still accepts such a payload where it did before. The supported FAST case caps axis bounds and endpoints at 64 and bin counts at 2^20, and requires each nonzero axis value to have magnitude at least 2^-100. A regular-bin width is at least about 2^-172, and finite clamped differences and indices stay in range. Variable-bin comparisons see only bounded normal or zero values. Flow behaviour is the existing lookup's behaviour.

For an eligible 12-lane non-RHO row, raw ID/ME pT and CB pT and relevant coordinates must be zero or normal and bounded. Map-cell and momentum magnitudes are at most 2^100 and at least 2^-100 when nonzero. Eta has magnitude at most four. The only sinh is in the ID term, with magnitude below 2^5. The weakest nonzero squared resolution term is on the order of 2^-800 or greater and the largest is below 2^430. The sum of squares is nonnegative, and the original immediate return for a zero raw pT precludes a pole. The final product with CB pT stays in the normal range or is exactly zero. The omitted terms can set the standard inexact flag within this envelope. The x86-64 GNU-libc guard requires nearest rounding in both FP units, masked exceptions, default SSE denormal handling, and an already sticky SSE inexact flag. It neither raises nor clears a flag. It makes no fast-path assumption about other platforms. Recheck signed zeros, NaNs, exception flags and errno with the delivered compiler and libc. A failed row, payload or environment check leaves the original calculations and errors in place. No blanket assumption that finite inputs are safe is used.

## Read counts and source staging

The two calls can read five maps for a nonzero ID pT and six for nonzero ME pT. The current file has at most 14,925 and 14,557 usable ID and ME links respectively in each state. Their maximum is 4,697,043 reads in all 29 states or 4,373,109 outside the two retained RHO states. The latter represents at most 30.0% of the 14,592,510 original map reads counted in CP8. Active tracks with zero pT and other fallback rows lower the count. `ATLAS_MUON_M13_STATS` adds per-thread production omitted and retained rows, trace rows, exact omitted expected-lookup counts for omitted rows, and actual lookup attempts in the original two calls. The ordinary compilation excludes all increments and exports. Matched timing remains distinct from the read reduction.

Keep the accepted binaries and original provider receipt. Confirm the accepted source and patch context, archive it into the trial source directory, and apply M13 there. Select the currently retained control provider and keep its compiler, SDK, dependencies and runtime settings for the candidate. The stock provider build entry point is `atlas/muon/build-r01-stock-06.sh` in the isolated campaign checkout. Run the authorized commands pinned to CPU 18 with nice level 10 from the runtime owner. In the examples, `$fresh` is a fresh result directory, `$trial` is the staged directory for these source files, and `$repo`, `$sdk`, `$fixtures` and `$oracle` are the existing `atlas_al9` campaign paths.

```bash
git -C "$repo" archive 92be0fc35363c37a3a7a21cdf4d0c6bb2ece6623 -- atlas/muon | tar -x -C "$fresh/source"
git -C "$fresh/source" apply --check "$trial/M13.patch"
git -C "$fresh/source" apply "$trial/M13.patch"
candidate_src="$fresh/source/atlas/muon"
build="$repo/atlas/muon/build-r01-stock-06.sh"
taskset -c 18 nice -n 10 bash "$build" "$fresh/candidate" "$sdk" "$fixtures" "$oracle" "$candidate_src"
taskset -c 18 nice -n 10 bash "$build" "$fresh/instrumented" "$sdk" "$fixtures" "$oracle" "$candidate_src" \
    -DATLAS_MUON_M13_STATS
candidate="$fresh/candidate/provider-build/libatlas_muon.so"
instrumented="$fresh/instrumented/provider-build/libatlas_muon.so"
nm -D "$instrumented" | rg atlas_muon_m13_
if nm -D "$control" "$candidate" | rg -q atlas_muon_m13_; then exit 1; fi
```

Keep the existing clean standalone environment and CPython 3.14 interpreter for the Python evidence. Build the small ABI 4 C++ fixture with the same SDK CMake package and stock compiler. It loads each provider into a separate process. The fixture compares all native words or thrown exception classes/messages, including preexisting sticky flags, new standard exception flags, current rounding/traps and errno. It exercises normal MC, data, both RHO directions, zero and absent pT, directed rounding, an enabled overflow trap on ordinary input, nonfinite track and source angles, signed zero, tiny angles/pT, unknown state and skipped calibration. It also supports execution with a deliberately unproved test-only payload. The test-only counts output is on stderr.

```bash
taskset -c 18 nice -n 10 cmake -S "$trial" -B "$fresh/fenv-probe" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
    -Dcorrectionlib_DIR="$sdk/lib/python3.14/site-packages/correctionlib/cmake"
taskset -c 18 nice -n 10 cmake --build "$fresh/fenv-probe" --parallel 1
probe="$fresh/fenv-probe/m13_fenv_probe"
run=(taskset -c 18 nice -n 10 "$sdk/bin/python")
for name in control candidate instrumented; do
    extra=()
    if [[ "$name" == instrumented ]]; then extra=(counts); fi
    taskset -c 18 nice -n 10 "$probe" "${!name}" "$prod" "$trace" "${extra[@]}" \
        > "$fresh/fenv-$name.tsv" 2> "$fresh/fenv-$name.counts.tsv"
done
cmp "$fresh/fenv-control.tsv" "$fresh/fenv-candidate.tsv"
cmp "$fresh/fenv-control.tsv" "$fresh/fenv-instrumented.tsv"
"${run[@]}" "$trial/probe_counts.py" unproved-map "$prod" "$fresh/unproved-map.json"
taskset -c 18 nice -n 10 "$probe" "$control" "$fresh/unproved-map.json" "$trace" > "$fresh/unproved-control.tsv"
taskset -c 18 nice -n 10 "$probe" "$instrumented" "$fresh/unproved-map.json" "$trace" counts \
    > "$fresh/unproved-candidate.tsv" 2> "$fresh/unproved.counts.tsv"
cmp "$fresh/unproved-control.tsv" "$fresh/unproved-candidate.tsv"
```

Confirm `nominal` takes the eligible 12-lane path on the frozen calibration. The instrumented 12-lane `rho_*`, `downward`, `upward`, `overflow_trap`, `tiny_eta`, `tiny_id_pt`, `eta_flow` and nonfinite track rows must retain the original path if they reach it. The 40-lane rows and the mutated-map 12-lane rows must never omit the original calculations. `nan_mu_eta`, `unknown_state` and `skipped_call` exercise exit paths before those calculations.

```bash
driver="$repo/atlas/muon/columnar/run_muons.py"
check="$trial/probe_counts.py"
for name in control candidate; do
    "${run[@]}" "$check" lanes "${!name}" "$prod" "$driver" "$INPUT" \
        "$fresh/full12-$name.npz" "$fresh/full12-$name.json"
    "${run[@]}" "$check" lanes "${!name}" "$trace" "$driver" "$INPUT" \
        "$fresh/full40-$name.npz" "$fresh/full40-$name.json" --trace
done
for width in 12 40; do
    "${run[@]}" "$check" compare "$fresh/full$width-control.json" "$fresh/full$width-candidate.json" \
        "$fresh/full$width-comparison.json"
done
"${run[@]}" "$check" campaign "$instrumented" "$prod" "$driver" "$INPUT" \
    "$fresh/mechanism-hist.npz" "$fresh/mechanism-counts.json"
cmp "$fresh/mechanism-hist.npz" "$accepted_histogram"
taskset -c 18 nice -n 10 bash "$repo/atlas/muon/validate-root-free-06.sh" \
    "$fresh/original-release" "$acceptance" "$sdk" "$candidate" "$bridge"
```

The 12- and 40-lane audits hash all native binary64 words by lane and state, yielding 5,218,956 and 17,396,520 words respectively. Verify the independent A comparisons and exact histogram arrays including both flows. Retain original finite/exceptional classes and invalid input errors. Exercise the normal C runner for empty and alternate chunks, including real forward ID. Compare uninstrumented matched complete production timings only after the preceding checks pass and the observed map reduction is material. The existing matched driver uses the same input, 2,000-entry chunks, 97 states, compiler and accepted output. Preserve one bounded repeat at most under the campaign's existing timing rules.

```bash
"${run[@]}" "$repo/atlas/muon/benchmark_columnar_pairs.py" "$fresh/pairs-01" \
    "$INPUT" "$control" "$candidate" "$prod" "$accepted_histogram"
```

If retained, a new standalone bundle needs a fresh provider hash, identity, closure receipt, and exact `full`, `nominal`, and `stored` validation. Neither the wheel contract nor the visible demo needs source changes for this trial. Build, fixture, acceptance and timing results remain pending authoritative execution.
