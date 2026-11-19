# Figures from the muon validation runs

The extraction command reads the full matched observer files and actual endpoint histogram arrays. It runs outside all timing intervals in `atlas_al9`, using the campaign's existing uproot, NumPy, Awkward, hist and Typer environment. It checks the audited 20,000 events, 14,997 original muons, jagged row structure, available event and muon identifiers, and the 97-state release order. It links data and comparison inputs by absolute path and SHA-256. Use the comparison record and runtime identity for the same candidate artifact.

```bash
worker=/absolute/staged/worker
python "$worker/figures/extract_plot_data.py" \
  --reference /absolute/A/reference.root \
  --reference-systematics /absolute/A/hist-reference.root \
  --reference-audit /absolute/A/full-observer-audit.json \
  --reference-histogram /absolute/A/observer-histogram.npz \
  --candidate /absolute/C/observer.root \
  --candidate-histogram /absolute/C/histogram.npz \
  --comparison /absolute/C/compare.json \
  --implementation-evidence /absolute/C/loaded-runtime-identity.json \
  --acceptance /absolute/campaign/acceptance.json \
  --path-label C --output /absolute/results/C-plot-data.json.gz
```

Repeat for B with `--path-label B --candidate-systematics /absolute/B/hist-reference.root`. Supply the actual compare-run paths and repeat the implementation evidence option for separate provider, payload or build receipts as needed. Each pair stays independently attributable. A rejected pair with matched identities and complete saved fields can be extracted for diagnosis. The renderer labels it outside acceptance.

Copy the small gzip JSON bundles to the Windows report workspace. The independent renderer uses only these bundles, NumPy, Matplotlib and Typer. It opens no ROOT files.

```bash
python /absolute/worker/figures/render_agreement.py \
    /absolute/C-plot-data.json.gz \
    --output /absolute/report/figures
```

Supply the B bundle as another positional argument after B acceptance, and use a new output directory. The first figure and Markdown index link to the complete appendix. Detailing every state is optional with `--detail-all`. The default always includes an all-state map for every quantity, nominal detail for all quantities, representative kinematic variations and any state with out-of-tolerance object values or exact-field failures.

## Reading the figures

- `agreement-main` is the nominal comparison of combined momentum and the selected combined object factor. The matching overlaid curves retain their actual counts. The two lower panels show candidate-minus-release bin counts and the observed signed extrema of matched object residuals in each release bin. Empty bins carry no paired point. Exactly equal finite pairs are called out directly. Pair maxima include underflow and overflow.
- The appendix covers the three calibrated transverse momenta, energy, unchanged eta/phi, three individual factors, combined factor, three charge outputs, all saved selection masks and decisions, bad-muon, calibration validity and four tool statuses. A/B/C scale factor comparisons use the same source objects. Individual scale factors use the release call population, including out-of-validity returns. The combined factor uses the release final selection. Skipped rows and out-of-validity counts are recorded with the relevant distributions. See the full-range scale-factor pages for tails outside the narrow nominal panel.
- `agreement-thresholds` gives nominal decisions near the calibrated 3 GeV and raw eta boundaries. The calibrated pT panel uses the same original eta-accepted objects for every path. The archive covers the pT threshold region for each kinematic state. Each binary decision is paired by original object.
- `agreement-endpoint` uses the actual frozen weighted pT histogram. The lower panels show the signed differences in sumw and sumw² in each in-range bin. Flow counts and sums appear below the figure. The endpoint-by-state plot and CSV include both flow bins when calculating maximum residuals.
- The all-state maps distinguish exact numerical equality, equal values with unequal stored bits, differences within tolerance, and failed pairs. A dot identifies a nominal alias. The CSV tables report the effective state, signed extrema, finite value ranges, exceptional classes and category counts. Nonzero tolerance fractions use the frozen per-value limit in source units. No cross-variable physical residuals are mixed.

The public momentum status is derived from the raw eta call gate and the calibration validity mask for A and B. The C comparison uses the separately recorded status where available. The duplicate nominal charge branch is represented by the state-resolved charge decoration used in the acceptance comparison. A’s observer saved decisions and status masks. It did not save intermediate d0 significance, projected z0, per-write availability flags or momentum-node traces as independent reference observables. The report should link the boundary tests and additional acceptance evidence for those checks. The plotting data cannot establish performance or agreement for a different RNG stream.

Inspect the PNG and PDF pages after real extraction. For the report and talk, retain the relevant figure assets, compact plot data, figure provenance record, state summary tables and generator sources.
