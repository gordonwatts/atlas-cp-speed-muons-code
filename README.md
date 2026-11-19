# ATLAS muon calibration with CorrectionLib

This repository preserves the source history of the isolated Run 3 PHYSLITE muon study. **A** is the original AnalysisBase 25.2.117 workflow. **B** uses the shared CorrectionLib provider through the AnalysisBase framework bridge. **C** reads the original PHYSLITE directly with uproot and runs the same prescription in a standalone Python workflow. See the [companion results](https://github.com/gordonwatts/atlas-cp-speed-muons) for the report, plots, configurations, timing records and execution logs.

## Repository map

| Path | Purpose |
| --- | --- |
| [`atlas/muon/provider.cxx`](atlas/muon/provider.cxx) and [`atlas/muon/CMakeLists.txt`](atlas/muon/CMakeLists.txt) | Provider registration and build of the shared CorrectionLib nodes |
| [`atlas/muon/momentum/`](atlas/muon/momentum) | Momentum calibration and smearing, ROOT-compatible random draws, covariance reuse, exporters and numerical fixtures |
| [`atlas/muon/efficiency/`](atlas/muon/efficiency) and [`atlas/muon/selection/`](atlas/muon/selection) | Efficiency and selection prescriptions and their configuration |
| [`atlas/muon/framework/`](atlas/muon/framework) | Path B bridge, release symbol bindings, launch and dispatch verification |
| [`atlas/muon/columnar/`](atlas/muon/columnar) and [`atlas/muon/portable/`](atlas/muon/portable) | Path C reader and output code, standalone script template, package assembly and install auditing |
| [`tools/muon_reference/`](tools/muon_reference) | Path A analysis configuration, reference drivers, observer, histogramming and comparison helpers |
| [`atlas/muon/`](atlas/muon) | Build, export, benchmark and validation entry points, including [the final A/B/C bracket](atlas/muon/run-final-bracket.sh) |
| [`campaign/inspection/`](campaign/inspection) | Supplemental campaign inspection files, including patches and probes for experimental changes |
| [`campaign/licenses/`](campaign/licenses) | Athena and ROOT source licensing and provenance |
| [`src/`](src), [`include/`](include), [`tests/`](tests) | The inherited CorrectionLib evaluator, headers and test suite |

## Optimization history

The parent history is the [pinned CorrectionLib baseline](https://github.com/gordonwatts/correctionlib/tree/3dcfa20ec01fb40c4d5fec58383785519dec1c00). The campaign commits run from that revision through [`450a491`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/tree/450a491070be2aba4b6b70050dfa2f2396242030) with their original hashes and parents. The standalone assembly was pinned to [`92be0fc`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/commit/92be0fc35363c37a3a7a21cdf4d0c6bb2ece6623).

| Commit | Retained change |
| --- | --- |
| [`8864a5c`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/commit/8864a5ca57593d72620d09ea9f944721dc9a3d6b) | Reuse the six original Gaussian draws for a matching seed |
| [`fb10b1d`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/commit/fb10b1d972727354e8b9fd8f123e3ec748265050) | Reuse the track-covariance decomposition within each muon and state |
| [`e9707bc`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/commit/e9707bc18b16e24e8202bbbf7e7d9da97da08993) | Remove ROOT at runtime while preserving the original nonzero-seed stream |
| [`1116e1e`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/commit/1116e1e02fc9dcef8e218d2e11ee4e5b673bd893) | Reuse covariance factors across states with exactly equal matrices |
| [`a3ad717`](https://github.com/gordonwatts/atlas-cp-speed-muons-code/commit/a3ad7179f0ae89bc7a2adc57cda3771a6e1a2aa2) | Reuse the filtered systematic set in the framework bridge |

For a clone with the evaluator dependencies, use `git clone --recurse-submodules https://github.com/gordonwatts/atlas-cp-speed-muons-code.git`. The reference jobs require the pinned AnalysisBase release and original CVMFS input. The standalone build and export also use the recorded calibration inputs and toolchain. See the [reference setup](https://github.com/gordonwatts/atlas-cp-speed-muons/blob/main/reference-setup.md) and [final build and run evidence](https://github.com/gordonwatts/atlas-cp-speed-muons/tree/main/evidence/checkpoint-17/reproduction) for the executed configuration and paths. The upstream [CorrectionLib README](https://github.com/gordonwatts/correctionlib/blob/3dcfa20ec01fb40c4d5fec58383785519dec1c00/README.md) describes the evaluator itself.
