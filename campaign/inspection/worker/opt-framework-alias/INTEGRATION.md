# Path B momentum alias resolution

The old bridge calls the genuine `MuonCalibTool::applySystematicVariation` and then rebuilds the tool's affecting set and filters the requested set for its own dispatch. Genuine release code applies each constituent tool and owns the status. The bridge's second pass is used to name the route to the CorrectionLib node. Release `affectingSystematics()` constructs and merges fresh constituent sets. The release subtools already maintain their own systematic caches. The M01-era B profile sampled variation construction, insertion, membership comparison, and basename creation. Those symbols can also run in other algorithms, so their sample shares cannot be assigned wholly to the redundant bridge pass. [Release tool](../../source/MuonCalibTool.cxx) · [Release subtool cache](reference/SystematicsCache.h) · [B profile](../../evidence/checkpoint-04/m01-profile-B-01/exclusive-symbols.csv)

The saved 100-event B smoke counted 2,901 successful bridge setter dispatches. That is one initialization setter plus 29 kinematic states per event. The existing source performs an aggregate affecting-set construction and filter on every one. The full 20,000-event run is expected to execute 580,001 genuine setters, with the 68 weight-only settings handled by the original systematics and scale-factor configuration. A successful candidate audit should see one aggregate catalog construction and 29 filters for the full run. These are structural predictions to check against native calls. They do not establish wall-time improvement. [Original dispatch evidence](../../evidence/checkpoint-03/dispatch-counts.json) · [Calibration algorithm](../../source/MuonCalibrationAndSmearingAlg.cxx)

## Lifetime and exact state handling

`B-alias-cache.patch` adds binding for the release initialize entry point and leaves the genuine setter first on every invocation. Release failures return immediately with the original status. Every cold dispatch still runs the original `CP::SystematicSet::filterForAffectingSystematics`. The cache keeps successful names only. CP filtering determines whether an unaffected or mixed request resolves to nominal, an existing momentum variation, a combined set, or failure. A combined affecting name passes through unchanged, so the existing CorrectionLib state validation owns unsupported physics routes. Failed calls leave the last successful alias as before.

The key owns the exact ordered set members. It uses CP's cached hash but compares each variation name and the sequence length. The pinned CP equality compares only the dash-joined name. The stronger equality prevents a dash-containing variation from reusing the result for a set of other members with the same spelling when joined. Neither the input state's address nor a hash alone authorizes reuse. The cache retains up to 128 successful requests. Unseen requests beyond that bound still use CP filtering. [Pinned SystematicSet implementation](reference/SystematicSet.cxx) · [Pinned SystematicSet interface](reference/SystematicSet.h)

The cache is enabled only when the named bridge tool initializes with the frozen settings and its release-internal calibration handles are still empty for genuine construction. Configurations that supply their own child handles continue rebuilding the aggregate and filtering per call. The frozen release children derive their affecting sets from initialization-time properties. This trial uses the pinned one-thread run with settings fixed between initialization calls. The initialization wrapper makes the new instance available to the genuine nominal setter after genuine child retrieval. Upon success the AnalysisBase component owns the cache through `addCleanup`. A thread-local map holds only weak references. Failed initialization removes its entry, subsequent initializations replace it, and destroyed objects cannot be read by a newly created object at the same address. Only the named B component is involved. A, the provider, payloads and C runtime stay unchanged. [AnalysisBase ownership API](reference/AsgComponent.h) · [Tool handle configuration](reference/AnaToolHandle.h)

## Build and test

Apply the patch to the source matching the currently accepted B library. Keep the same loaded CP release, compiler ABI, Columnar definitions and linked libraries, including the authoritative `ColumnarCore` fix. Capture the original and new bridge hashes.

```bash
git -C "$repo" apply --check "$trial_source/B-alias-cache.patch"
git -C "$repo" apply "$trial_source/B-alias-cache.patch"
taskset -c 18 nice -n 10 bash "$build_bridge" "$fresh/b-alias-build" "$sdk"
ATLAS_MUON_B_ALIAS_PROBE=1 taskset -c 18 nice -n 10 \
  bash "$build_bridge" "$fresh/b-alias-probe" "$sdk"
candidate="$fresh/b-alias-build/libAtlasMuonFramework.so"
diagnostic="$fresh/b-alias-probe/libAtlasMuonFramework.so"
sha256sum "$control" "$candidate" "$diagnostic" "$provider" "$production_payload"
nm -D "$diagnostic" | rg atlas_muon_b_momentum_alias
```

Use the diagnostic bridge for the unmeasured native fixture. It links the genuine release and constructs an unmatched native control plus a bridge-named tool. It compares genuine status and original filtering for nominal, active momentum, unaffected weight, mixed weight/kinematic and mixed kinematic pairs, same joined name with different members, unsupported magnitudes and duplicate basenames. It checks that failed setters preserve the current alias and constructs valid, failed-init, then valid tool instances at one address. It exercises the actual genuine initialize path and the bridge's audit-only alias accessor.

```bash
taskset -c 18 nice -n 10 bash "$trial_source/build-alias-fixture.sh" "$fresh/alias-fixture" "$diagnostic"
ATLAS_MUON_B_ALIAS_COUNTS=1 taskset -c 18 "$fresh/alias-fixture/alias-fixture" \
  > "$fresh/alias-fixture.log" 2>&1
standalone=(env -u PYTHONPATH -u PYTHONHOME "$sdk/bin/python")
"${standalone[@]}" "$trial_source/check_alias_dispatch.py" "$fresh/alias-fixture.log" "$fresh/fixture-counts.json"
```

Perform the existing 100-event original CPRun dispatch check with the diagnostic library. Require the loaded initializer, setter and correction entry to resolve to this bridge. Verify the original configuration and count 2,901 release calls, 29 aggregate filters and 2,872 hits. Run full B with the same original mixed 97-state config and the diagnostic library in a fresh output directory. Confirm all 97 states are present and count 580,001 release calls, 29 filters and 579,972 hits. Use `ATLAS_MUON_B_ALIAS_COUNTS=1` to collect counts in the diagnostic library without the bridge entrypoint's loader-audit file limit. The new flag is present only in that build. Use the actual analysis child log, typically `job.log`, when checking counts.

```bash
"${standalone[@]}" "$trial_source/check_alias_dispatch.py" "$smoke_job_log" "$fresh/smoke-counts.json" --events 100
"${standalone[@]}" "$trial_source/check_alias_dispatch.py" "$full_job_log" "$fresh/full-counts.json" --events 20000
```

Independently verify mixed full B against A and the current accepted B with the ordinary candidate library, including state order, identity, statuses, systematic aliases, selections, individual/combined weights and histogram bytes. Check the unchanged provider and C wrapper/payload hashes and compare its accepted histogram bytes when replayed. Only if the lead queues timing, use the original matched B framework-only nominal/full timer, uninstrumented and audit-free on CPU 18. Keep the provider, payload, CPRun wrapper, input, cache conditions and A controls fixed. Alias count reductions explain the mechanism. The paired framework series determines its effect on the 1.20 budget.
