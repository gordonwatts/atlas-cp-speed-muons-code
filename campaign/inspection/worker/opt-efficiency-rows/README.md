# M26 compact efficiency inputs in C

`M26.patch` affects only C's efficiency and product materialization and its consumers. It applies to the CP03 developer runner and the current portable `muon_demo.py.in`. One `EfficiencyRows` packer in the shared `raw_input.py` serves both. The wheel uses that file through `assemble.py --raw-reader`. The native provider, payload, B path, branch projection and kinematic recipes are independent of this change.

## Row routing and scope

For each computed kinematic gate, gather the seven efficiency input arrays once. True rows keep their original local order. If any gate is zero, keep the first original zero-gate row as the last packed row. Reuse this input pack across the three tools and every weight-only alias with that same kinematic state. A distinct `(tool, kinematic state, efficiency state)` still evaluates the same CorrectionLib node with the same state. Empty input stays empty. An invalid gate leaves the full original ordered inputs for native evaluation.

The `ToolRoute` node in `atlas/muon/efficiency/efficiency.cc` checks exact requested-state membership before reading the gate. Its zero-gate path returns its configured skipped factor and skipped status without consulting any other input. The exported allowlist is a category keyed only on `state`. Both actions hold for every omitted zero-gate row of the same route, so the genuine representative retains the category error on nonempty all-skipped batches and supplies the actual two output lanes from the same node. Invalid gates still reach the native gate error. Existing state-before-gate precedence also holds for simultaneous invalid state and gate. No route data or skipped factor is introduced in Python.

All three tools receive the same selected indices and skipped representative for a given gate. The existing CorrectionLib `FloatProduct` therefore still combines the same three float factors in the same order, including the representative's skipped factors. Production uses the compact pT and selected product values directly for `hist` in their original order. The visible PEP 723 script still shows the efficiency and product calls, GeV conversion and histogram fills. Observation expands each distinct route's actual SF and code once and each combined result once, using the packed representative as the default and restoring selected rows at their original indices. This retains the expected per-object skipped bits and includes selected OOV outputs. No observer expansion runs in the timed production path.

The gathered inputs and selected indices live for the current chunk, alongside the existing state caches. For a mixed gate with `k` true rows the seven typed arrays occupy about `56 × (k + 1)` payload bytes and the retained indices use `8 × k` bytes on the pinned 64-bit environment. This excludes NumPy object headers and ephemeral masks or indices. Fully selected, empty and invalid-gate inputs retain existing array views. Compact per-tool outputs and product temporaries reduce native output materialization. The actual effect on total retention, CPU and elapsed time requires runtime evidence.

## Count targets

The checkpoint-08 captured dense C image used 1,550 native efficiency batches and 2,324,535 input rows. It spent `0.10104 + 0.07212 + 0.07246 ≈ 0.246` seconds in the three efficiencies and `0.01626` seconds in the product. The accepted observer establishes 850,902 actual selected efficiency calls. At the same 2,000-entry chunking, the candidate must still make those 850,902 selected calls and at most 1,550 additional skipped representative calls. The bound is 852,452 physical SF rows, at most 36.67% of the dense row count. The product has 970 state/chunk batches and 1,454,709 previous physical rows. Its proposed row count is the observed selected counts across 97 output states plus one representative for each nonempty state/chunk with skips. These are work-volume expectations. They do not establish a timing improvement. [Stock phase receipt](../../evidence/checkpoint-08/retained-stock-native-01/phases.json)

`measure_columnar_phases.py` uses the new result's histogram arrays and exposes selected and skipped native row counts per efficiency tool. Those counters run after the native timing stop in the diagnostic path. With the exact same provider, input, observer-free projection and chunk size, compare `native_batch_calls`, `native_lanes`, and each tool's `*_selected`, `*_skipped` and `*_batches_with_skips` counts. Physical rows must equal the sum of selected and skipped rows. Each candidate skipped count must equal its batches-with-skips count, and summed skipped rows must be at most the 1,550 original route batches. Compare unchanged histogram arrays, branch projection, and the unpacked momentum and selection row counts. Keep all instrumentation outside any performance trial.

## Runtime-owner handoff

Preserve the dense control checkout and candidate-independent provider and payload. Run `git apply --check "$trial_source/M26.patch"` then apply only to the candidate. The patch can be staged independently of reader projection and primary pack changes. The native build is the same approved provider. For the installed candidate, assemble the wheel with the patched reader identity and include the patched PEP 723 template. Keep that installed environment physically separate from the dense control. Use the same source artifact, one CPU, one reader thread and the adopted 2,000-entry chunks for the principal comparisons.

```bash
probe="$trial_source/probe_efficiency_rows.py"
run=(taskset -c 18 nice -n 10 "$sdk/bin/python")
PYTHONPATH="$repo/atlas/muon/columnar" "${run[@]}" "$probe" edges \
    "$provider" "$payload" "$fresh/m26-edges.json"
PYTHONPATH="$repo/atlas/muon/columnar" "${run[@]}" "$probe" observers \
    "$dense_observer" "$compact_observer" "$fresh/m26-observer-exact.json"
"${run[@]}" "$repo/atlas/muon/measure_columnar_phases.py" \
    "$INPUT" "$provider" "$payload" "$fresh/m26-phase.npz" \
    "$fresh/m26-phases.json" "$dense_histogram" --chunk-size 2000
```

The edge probe calls each genuine efficiency node and product through the ABI 4 Python API. It compares exact full and packed words for mixed gate order, every tool's nominal and one owned variation, a selected OOV result, an invalid run on skipped rows, all selected, all skipped and empty. It checks that nonempty all-skipped and mixed invalid categories throw the same errors as dense evaluation. It checks the existing zero-row invalid-state behavior with the loaded SDK, a malformed gate, and simultaneous state and gate errors. No stand-in numerical node is used.

Run all 20,000 entries in both controls with the real `observe` CLI and the same diagnostics allowed by the reference harness. The observer comparison checks exact existence, event and object order, jagged lengths and bits for 2,324,535 values apiece in the three per-tool SF, CP-status and call columns. It checks all 1,454,709 combined SF values across the 97 endpoints, the original gates, and the count of 850,902 true tool calls. Independently compare the complete C acceptance against release A under `acceptance.json`, plus histogram arrays between the candidate and dense control in full, nominal and stored modes. The installed visible entry point should reproduce those histograms and the normal closure audit. Check `--stop 0`, an empty event or range, and an alternate supported chunk size over identical ranges. Confirm that diagnostic and production histograms agree. Preserve exact input and output receipts before authorizing any elapsed/CPU comparison.

These artifacts were prepared in the Windows workspace. WSL build, edge execution, full-file acceptance and performance remain runtime-owner work.
