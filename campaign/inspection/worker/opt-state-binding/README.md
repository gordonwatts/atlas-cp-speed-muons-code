# M12 named-state map diagnostic

This source-only diagnostic measures the accepted momentum `std::map<std::string, State, std::less<>>` with a lookup per row and with state binding once per batch. It does not change the production provider, core, payload, or either caller.

In the preserved checkpoint-13 binary (ELF build ID `7b8c29dfdbd14e23d0910921fd79db362d10e887`, SHA256 `c020c412b974b9a1de46ab0888574a56c574260b256f186de1388dc1942cb3aa`), compiler inlining put two different equality paths under `MomentumNode::evaluate_impl`. The 24 samples at `0x1bd6f` are state-name comparison. The call comes near the start of the function, tests both equality and the sign of the result, and follows a red-black tree child. The 47 samples at `0x1e872`, `0x1e8e0`, `0x1e94e` and `0x1e9bc` are the four covariance-cache ways comparing the fixed 400-byte key. The 24 state samples are 0.29% of the 8,232 captured CPU samples, about 6.9 ms per full pass when scaled by the sampled CPU time. That estimate covers the comparison leaf only. It does not include inlined routing overhead around the comparison. [Preserved caller PCs and disassembly](../../evidence/checkpoint-13/provider-callsites-03)

`replay_inputs.py fixture` verifies the original input UUID and sizes and reads the untouched muon pT field for source multiplicities only. It derives first-use kinematic state order from the accepted `StatePlan` across the original 2,000-entry chunk partition. The native replay checks all ten ranges, all 29 named states, nominal routing, 290 batches and 434,913 row uses. Row identities carry no additional information for this lookup because each batch has one broadcast state and the original traversal is sequential. The ABI 4 caller's existing numeric/string variant materialization is outside this diagnostic and would remain part of any production test.

The harness builds the actual source `Payload` from the exact accepted CorrectionSet configuration. This loads the real immutable period maps and constructs the same state map and insertion ordering as the production object. Loading, input scan, exception probes and writing are outside the timed loops. The named loop uses `payload.states.at(batch.state)` once per row. The bound loop uses the same route once for every nonempty batch, then keeps its result local to that batch. Both exercise the action and direction on every original row with the same nonfoldable sink and check the full replay checksum. A focused untimed probe compares exact unknown-state `out_of_range` behavior and partial progress between known, empty unknown, nonempty unknown and later valid batches. It does not claim full native error parity for a production override.

There is one retained warm-up record excluded from analysis and five alternating measured pairs, with 32 replays per arm and timestamps for both monotonic wall and process CPU time. Report each paired named-minus-bound difference normalized to one full input. The declared screening threshold is `0.005 × 2.69 s = 13.45 ms`. Only if both lower endpoints of the five-pair Student intervals exceed the threshold does the isolated result justify considering a production M12 trial. Otherwise defer batch binding. This hot isolated map difference omits production cache interference, row marshalling and dispatch overhead. Treat it as a ranking measurement and do not claim end-to-end speedup from it.

## Runtime-owner commands

Use a fresh result directory `$fresh`, the staged source directory `$trial`, the retained stock GCC 11 source/SDK and the exact loaded checkpoint-13 production CorrectionSet SHA256 `2058e159cd2237bf5c846c2944287ce506886ef7caa17f93fb4d66b7b5f6270c`. Capture the source checkout/provider identity, compiler and payload, plus the fixture and executable hashes in the receipt. Use the campaign's existing clean environment and one CPU. `$repo`, `$sdk`, `$prod` and `$INPUT` are the campaign's authoritative paths.

```bash
run=(taskset -c 18 nice -n 10 "$sdk/bin/python")
"${run[@]}" "$trial/replay_inputs.py" fixture "$INPUT" "$repo/atlas/muon/columnar/run_muons.py" \
    "$fresh/batches.tsv" "$fresh/fixture.json"
taskset -c 18 nice -n 10 cmake -S "$trial" -B "$fresh/build" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
    -Dcorrectionlib_DIR="$sdk/lib/python3.14/site-packages/correctionlib/cmake" \
    -DMOMENTUM_SOURCE="$repo/atlas/muon/momentum" -DMUON_RAPIDJSON_INCLUDE_DIR="$repo/rapidjson/include"
taskset -c 18 nice -n 10 cmake --build "$fresh/build" --parallel 1
taskset -c 18 nice -n 10 "$fresh/build/m12_named_map_replay" "$prod" "$fresh/batches.tsv" > "$fresh/pairs.tsv"
"${run[@]}" "$trial/replay_inputs.py" summarize "$fresh/pairs.tsv" "$fresh/ranking.json"
sha256sum "$prod" "$fresh/batches.tsv" "$fresh/build/m12_named_map_replay" "$fresh/pairs.tsv" > "$fresh/sha256.txt"
```

If the result supports a full trial, use only the API 4 momentum extension's array batch entry point. For a validated batch the string lane broadcasts. Keep all `BatchInput::at` row materialization and validation in the original order. Preserve unknown state before row flags, zero rows without lookup, the complete 12 and 40 outputs, and unchanged scalar B. Validate mixed successive requests, malformed typed lanes, trace and no-calibration cases before considering matched complete C timing. No implementation has been staged here.
