"""Count the M13 guard on the original full workflow and prepare a fallback payload."""

import ctypes
import hashlib
import json
from pathlib import Path
import struct
import sys
from typing import Annotated

import numpy as np
import typer

app = typer.Typer(no_args_is_help=True)
File = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Output = Annotated[Path, typer.Argument(dir_okay=False)]
COUNTS = ("omitted_rows_12", "original_rows_12", "original_rows_40", "omitted_map_reads", "original_map_reads")


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(output, result):
    with output.open("x", encoding="utf-8") as report:
        json.dump(result, report, indent=2)
        report.write("\n")


@app.command()
def campaign(provider: File, payload: File, driver: File, original: File, histogram: Output, output: Output):
    """Record live eligibility, actual retained reads, and the avoided reads."""
    from correctionlib import _core  # Load the paired SDK before opening the instrumented provider.

    sys.path.insert(0, str(driver.resolve().parent))
    from run_muons import Mode, execute

    module = ctypes.CDLL(str(provider.resolve()))
    reset = module.atlas_muon_m13_reset_resolution_counts
    reset.argtypes, reset.restype = [], None
    read = module.atlas_muon_m13_read_resolution_counts
    read.argtypes, read.restype = [ctypes.POINTER(ctypes.c_uint64)], None
    counts = (ctypes.c_uint64 * len(COUNTS))()
    reset()
    execute(original, histogram, Mode.full, (provider,), payload, payload, payload, chunk_size=2000)
    read(counts)
    result = dict(zip(COUNTS, counts, strict=True))
    omitted, retained, trace, avoided_reads, retained_reads = counts
    assert trace == 0 and omitted + retained == 434913, result
    assert retained >= 2 * 14997 and omitted > 0, result
    assert 0 < avoided_reads <= 4373109 and avoided_reads + retained_reads <= 4697043, result
    save(output, {
        "counts": result, "expected_full_rows": 434913, "eligible_non_rho_row_bound": 404919,
        "two_estimate_read_bound": 4697043, "non_rho_read_bound": 4373109,
        "provider_sha256": digest(provider), "payload_sha256": digest(payload),
        "histogram_sha256": digest(histogram),
    })


@app.command()
def lanes(provider: File, payload: File, driver: File, original: File, histogram: Output, output: Output,
          trace: bool = False):
    """Hash the native output words in original order across every real state."""
    sys.path.insert(0, str(driver.resolve().parent))
    import run_muons
    from muon_analysis import MOMENTUM_OUTPUTS, NativeWorkflow, TRACE_OUTPUTS

    fields = MOMENTUM_OUTPUTS + TRACE_OUTPUTS if trace else MOMENTUM_OUTPUTS
    assert len(fields) == (40 if trace else 12)
    accumulators = {}
    state_counts = {}

    class Observer(NativeWorkflow):
        def __init__(self, providers, momentum_payload, selection_payload, efficiency_payload, _observe):
            super().__init__(providers, momentum_payload, selection_payload, efficiency_payload, trace)

        def calibrate(self, state, common, selection_inputs):
            result, selected = super().calibrate(state, common, selection_inputs)
            assert all(field in result for field in fields)
            by_lane = accumulators.setdefault(state, {field: hashlib.sha256() for field in fields})
            state_counts[state] = state_counts.get(state, 0) + len(result[fields[0]])
            for field in fields:
                by_lane[field].update(np.asarray(result[field], dtype="<f8").tobytes())
            return result, selected

    run_muons.NativeWorkflow = Observer
    run_muons.execute(original, histogram, run_muons.Mode.full, (provider,), payload, payload, payload, chunk_size=2000)
    assert len(state_counts) == 29 and set(state_counts.values()) == {14997}
    save(output, {
        "provider_sha256": digest(provider), "payload_sha256": digest(payload),
        "histogram_sha256": digest(histogram), "states": list(state_counts), "objects": state_counts,
        "fields": list(fields), "words": sum(state_counts.values()) * len(fields),
        "lane_sha256": {state: {field: bits.hexdigest() for field, bits in lane.items()}
                        for state, lane in accumulators.items()},
    })


@app.command()
def compare(control: File, candidate: File, output: Output):
    """Require bitwise equality of each state and output lane."""
    a, b = (json.loads(path.read_text()) for path in (control, candidate))
    assert a["provider_sha256"] != b["provider_sha256"]
    for field in ("payload_sha256", "histogram_sha256", "states", "objects", "fields", "words", "lane_sha256"):
        assert a[field] == b[field], field
    save(output, {
        "compared_states": len(a["states"]), "compared_lanes": len(a["fields"]), "exact_words": a["words"],
        "control_provider_sha256": a["provider_sha256"], "candidate_provider_sha256": b["provider_sha256"],
    })


@app.command()
def unproved_map(payload: File, output: Output):
    """Make a test-only payload with one finite cell below the certificate per period."""
    definitions = json.loads(payload.read_bytes())
    momentum, = (item for item in definitions["corrections"] if item["name"] == "muon_momentum")
    years = momentum["data"]["config"]["years"]
    assert len(years) == 8
    for year in years:
        year["expected"][0][2][0] = struct.pack(">d", 2.0**-101).hex()
    with output.open("x", encoding="utf-8") as report:
        json.dump(definitions, report, separators=(",", ":"))
        report.write("\n")


if __name__ == "__main__":
    app()
