# M18a reuse of nominal integer histogram counts

This source-only candidate starts from the public CP13 ZIP, `atlas-muon-standalone-0.1.dev3-cp314-linux_x86_64.zip`, at delivery commit `92be0fc35363c37a3a7a21cdf4d0c6bb2ece6623`. The original member `muon-standalone/bundle/muon_demo.py` has SHA-256 `8917d630bb28788f6fe2347904e1aaffe47b9b533dcde47eb07485ab12a67881`. Applying `M18a.patch` to that member produces SHA-256 `63d989e5bba673531334fcaaf49296cfd58cce8e28f131b77672f996409f0115`. The package workflow has SHA-256 `bf2afdced2b7e414e163d5bb0ee3ee2c99a85f40ed13a349c8ded5f2ba8366ab`. Keep the published ZIP and installed wheel environment as the control. [Accepted archive receipt](../../evidence/checkpoint-13/final-standalone-01/archive-sha256.txt) · [Installed workflow identity](../../evidence/checkpoint-13/final-standalone-01/identity.json)

## Why the copy is equivalent

The installed workflow declares 97 distinct labels in output order, consisting of `NOSYS`, 28 varied kinematic states and 68 disjoint weight-only states. Each weight-only state consumes the same `m, gate` pair from `kinematics["NOSYS"]` as nominal. Its selected momenta are the same binary64 values in the same order. Its weights can differ. Only its `Int64` fill is redundant.

M18a keeps the existing nominal and varied-kinematic Int64 fills, then broadcasts the completed nominal integer column to the 68 destination columns once per pass. The destination labels retain the original order. StrCategory is created from that ordered list with `growth=False, flow=False`. The pT axis has 100 fixed bins and both flow bins. `unweighted.view(flow=True)` exposes mutable Int64 storage with shape `(102, number_of_states)`. The nominal index comes from that histogram's category axis. It is distinct from every destination. There is no subsequent fill after acquiring the view. Destination assignment copies all integer counts, including both flows, without rebinding a histogram axis or storage. [Hist axis semantics](https://hist.readthedocs.io/en/latest/user-guide/axes.html) · [Boost.Histogram flow and view semantics](https://boost-histogram.readthedocs.io/en/latest/user-guide/histogram.html#views)

The full pass processes ten 2,000-entry chunks. The installed loop performs 970 Int64 and 970 weighted fill calls. The candidate reduces Int64 calls to 290, avoiding 680 fills and 373,320 selected visits to the integer pT axis, from 68 states times 5,490 nominal selected muons. It writes 6,936 Int64 destinations from the one nominal column, or 55,488 destination bytes, once per pass. This is source-level work accounting. It does not estimate a wall-time saving. [Full installed-script output counts](../../evidence/checkpoint-13/final-standalone-01/uv-full.stdout)

Momentum, selection, efficiency, status and finite-input checks still run in their original locations for every requested state. Weighted fill calls and their order, momenta and weight arrays stay the same. Nonfinite selected pT or weight still raises before publication, including a nonfinite weight in a weight-only category. The stored branch retains its distinct raw-pT finite guard, weighted unit fills and Int64 fill. Nominal-only and stored construct one `NOSYS` column and have no copy destinations. An empty chunk adds zero to every bin, while a no-chunk pass copies zero-filled nominal bins to the aliases. The copy also skips labels explicitly classified as kinematic and is disabled if the requested category list lacks `NOSYS`.

## Where output is written

In the original archive member, lines 122–128 build the axis order, 130–245 define `fill_pass`, 249–263 compute and compare per-pass histograms, and 265–271 publish the NPZ. The candidate finalizes the integer columns at the end of `fill_pass`, inside its elapsed and CPU intervals. Every repeat starts from fresh histograms, and the repeated digest includes finalized `count`, `sumw` and `sumw2`. After all repeats agree, the same visible script exclusively opens the requested output and calls `np.savez_compressed` with `states`, `pt_edges_GeV`, `count`, `sumw` and `sumw2`. It publishes the final repetition only. Nothing changes in the native provider, weights, wheel contents, launcher contract or the accepted archive.

## Stage and verify after the baseline identifies a measurable cost

The appropriate baseline is the actual installed visible script and the original file, with line-level measurements separating the weighted call, unweighted call and array preparation. Use those baseline observations to decide whether M18a warrants a trial. The following are runtime-owner commands for fresh paths after authorization.

```bash
mkdir "$fresh/control" "$fresh/candidate"
unzip -p "$archive" muon-standalone/bundle/muon_demo.py > "$fresh/control/muon_demo.py"
cp "$fresh/control/muon_demo.py" "$fresh/candidate/muon_demo.py"
patch --dry-run -d "$fresh/candidate" -p0 < "$trial_source/M18a.patch"
patch -d "$fresh/candidate" -p0 < "$trial_source/M18a.patch"
sha256sum "$fresh/control/muon_demo.py" "$fresh/candidate/muon_demo.py"
```

The probe executes the actual histogram, guard and NPZ statements extracted from both files. It uses the installed Hist and NumPy on synthetic linked kinematics and weights, without loading calibration corrections. It compares exact NPZ bytes, original label and axis order even with nominal last, fixed axis traits, integer type, writable flow storage, weighted differences, both flows, selected and unselected nonfinite values, nominal/stored, no chunks, empty chunks and two independent passes. Run it with the accepted environment and the package's installed workflow path.

```bash
control="$fresh/control/muon_demo.py"
candidate="$fresh/candidate/muon_demo.py"
workflow=$("$installed_python" -c 'import pathlib, atlas_muon_portable; print(pathlib.Path(atlas_muon_portable.__file__).parent / "workflow.json")')
"$installed_python" "$trial_source/probe_hist_counts.py" "$control" "$candidate" "$workflow"
```

Use the original frozen input for the end-to-end check with the same installed CPython and wheels. Require exact state order, pT edges, integer bins, both weighted arrays and saved NPZ bytes. Check original file identity and matching pass digests. The alternate chunk changes only the reader partition in both variants. Cross-check the full output against the frozen accepted Path A histogram using the existing exact comparison.

```bash
for role in control candidate; do
    for mode in full nominal stored; do
        "$installed_python" "${!role}" "$INPUT" "$fresh/$role-$mode.npz" --mode "$mode" \
          > "$fresh/$role-$mode.stdout"
    done
    "$installed_python" "${!role}" "$INPUT" "$fresh/$role-empty.npz" --mode full --stop 0 \
      > "$fresh/$role-empty.stdout"
    "$installed_python" "${!role}" "$INPUT" "$fresh/$role-repeat.npz" --mode full -n 2 --chunk-size 173 \
      > "$fresh/$role-repeat.stdout"
done
for mode in full nominal stored empty repeat; do
    cmp "$fresh/control-$mode.npz" "$fresh/candidate-$mode.npz"
done
cmp "$fresh/candidate-full.npz" "$fresh/candidate-repeat.npz"
```

Only after acceptance, use a fresh matched full C series with uninstrumented visible scripts and the accepted tool, input, payload, worker placement, cache and process-clock rules. Leave the weighted loop and other proposed histogram mechanisms outside this trial.
