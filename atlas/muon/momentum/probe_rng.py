"""Test the M01 cache against the unchanged provider and release draws."""

import ctypes
import hashlib
import json
from pathlib import Path
import runpy
import sys
from typing import Annotated

import numpy as np
import typer

app = typer.Typer(no_args_is_help=True)
File = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Output = Annotated[Path, typer.Argument(dir_okay=False)]

CACHE_SLOTS = 8192
FIRST_SEED = 1001
COLLIDING_SEED = FIRST_SEED + CACHE_SLOTS
SECOND_SLOT_SEED = FIRST_SEED + 1
COUNT_NAMES = ("hits", "misses", "overwrites", "zero_bypasses")
SEED_FIELDS = ("g0", "g1", "g2", "g3", "g4", "g_high_pt")

# The canonical order first proves a hit across states and a hit from a
# different event with the same calculated seed, then two explicit overwrites.
SEQUENCE = (
    ("initial_miss", FIRST_SEED - 1, 0., "NOSYS", 1, FIRST_SEED),
    ("state_hit", FIRST_SEED - 1, 0., "MUON_SCALE_ID__1up", 1, FIRST_SEED),
    ("event_hit", FIRST_SEED - 126, 0.125, "NOSYS", 1, FIRST_SEED),
    ("slot_overwrite", COLLIDING_SEED - 1, 0., "NOSYS", 1, COLLIDING_SEED),
    ("original_key_was_evicted", FIRST_SEED - 1, 0., "NOSYS", 1, FIRST_SEED),
    ("skipped", COLLIDING_SEED - 1, 0., "NOSYS", 0, None),
    ("hit_after_skip", FIRST_SEED - 1, 0., "NOSYS", 1, FIRST_SEED),
    ("empty_slot_miss", SECOND_SLOT_SEED - 1, 0., "NOSYS", 1, SECOND_SLOT_SEED),
)
COUNTS_AFTER_STEP = (
    (0, 1, 0, 0),
    (1, 1, 0, 0),
    (2, 1, 0, 0),
    (2, 2, 1, 0),
    (2, 3, 2, 0),
    (2, 3, 2, 0),
    (3, 3, 2, 0),
    (3, 4, 2, 0),
)


class Statistics:
    def __init__(self, provider):
        self.library = ctypes.CDLL(str(provider.resolve()))
        self.read = self.library.atlas_muon_m01_rng_cache_counts
        self.read.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
        self.read.restype = None
        self.reset = self.library.atlas_muon_m01_rng_cache_reset
        self.reset.argtypes = []
        self.reset.restype = None
        self.bytes = self.library.atlas_muon_m01_rng_cache_bytes
        self.bytes.argtypes = []
        self.bytes.restype = ctypes.c_size_t
        self.draw = self.library.atlas_muon_m01_rng_probe_seed
        self.draw.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_double)]
        self.draw.restype = None

    def snapshot(self):
        counts = (ctypes.c_uint64 * len(COUNT_NAMES))()
        self.read(counts)
        return dict(zip(COUNT_NAMES, counts, strict=True))

    def zero_seed(self):
        values = (ctypes.c_double * len(SEED_FIELDS))()
        self.draw(0, values)
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite values from ROOT zero-seed probe")
        return bits(values)


def bits(values):
    unsigned = np.asarray(values, dtype=np.float64).view(np.uint64)
    return [f"{int(word):016x}" for word in unsigned]


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path, values):
    with path.open("x", encoding="utf-8") as output:
        json.dump(values, output, indent=2)
        output.write("\n")


def rows_and_fields(contract):
    fields = {name: index for index, name in enumerate(contract["outputs"])}
    input_spec = contract["inputs"]
    source_rows = {}
    for label, event, eta, syst, called, seed in SEQUENCE:
        inputs = {
            "systematic": syst, "called": called,
            "run_number": 410000, "event_number_lo": event, "event_number_hi": 0,
            "is_simulation": 1, "muon_type": 0,
            "mu_pt": 24321.0, "mu_eta": eta, "mu_phi": 0.0, "mu_charge": 1.0,
        }
        row = [inputs.get(item["name"], 0.0 if item["type"] == "real" else 0) for item in input_spec]
        source_rows[label] = row, seed
    return source_rows, fields


def batch(correction, contract, rows):
    arguments = []
    for index, item in enumerate(contract["inputs"]):
        if item["type"] == "string":
            if any(row[index] != "NOSYS" for row in rows):
                raise ValueError("The batch probe uses one systematic per native call")
            arguments.append("NOSYS")
        else:
            dtype = np.int64 if item["type"] == "int" else np.float64
            arguments.append(np.asarray([row[index] for row in rows], dtype=dtype))
    return correction.evalv(*arguments)


@app.command("release-oracle")
def release_oracle(output: Output):
    """Record original TRandom3 draws using the initialized release PyROOT."""
    import ROOT

    vectors = {}
    for seed in (FIRST_SEED, COLLIDING_SEED, SECOND_SLOT_SEED):
        random = ROOT.TRandom3()
        random.SetSeed(seed)
        vectors[str(seed)] = bits([random.Gaus(0, 1) for _ in SEED_FIELDS])
    save(output, {"root_version": str(ROOT.gROOT.GetVersion()), "vectors": vectors})


@app.command()
def edges(
    provider: File, payload: File, contract: File, oracle: File, output: Output,
    instrumented: Annotated[bool, typer.Option()] = False,
):
    """Compare native scalar and batch rows and measure exact cache branch counts."""
    from correctionlib import _core, plugins

    plugins.load_library(provider.resolve())
    correction = _core.CorrectionSet.from_file(str(payload))["muon_momentum"]
    specification = json.loads(contract.read_text(encoding="utf-8"))
    truth = json.loads(oracle.read_text(encoding="utf-8"))
    rows, fields = rows_and_fields(specification)
    if correction.output_size != len(fields) or len(fields) != 40:
        raise ValueError("Use the original 40-component diagnostic momentum payload")
    if [item.name for item in correction.inputs] != [entry["name"] for entry in specification["inputs"]]:
        raise ValueError("Correction input order differs from the accepted contract")

    cache = Statistics(provider) if instrumented else None
    if cache is not None:
        cache.reset()
    observed = {}
    mechanism = {}
    for (label, (row, seed)), expected_counts in zip(rows.items(), COUNTS_AFTER_STEP, strict=True):
        value = correction.evaluate_array(*row)
        draws = value[[fields[key] for key in SEED_FIELDS]]
        if seed is not None:
            assert value[fields["root_seed"]] == seed and value[fields["status"]] == 2
            assert bits(draws) == truth["vectors"][str(seed)], label
        else:
            assert value[fields["status"]] == -1 and np.isnan(draws).all(), label
        observed[label] = bits(value)
        if cache is not None:
            counts = cache.snapshot()
            assert tuple(counts.values()) == expected_counts, (label, counts)
            mechanism[label] = counts

    # Leave the table warm across all cases. Compare the common rows by identity
    # after reordering, selecting a subset and breaking at different boundaries.
    reordered = ("empty_slot_miss", "slot_overwrite", "skipped", "event_hit", "initial_miss")
    for boundaries in ((0, 5), (0, 2, 5), (0, 1, 3, 5)):
        for first, last in zip(boundaries, boundaries[1:]):
            labels = reordered[first:last]
            values = batch(correction, specification, [rows[name][0] for name in labels])
            assert values.shape == (len(labels), 40)
            for label, value in zip(labels, values, strict=True):
                assert bits(value) == observed[label], (label, boundaries)
    assert batch(correction, specification, []).shape == (0, 40)

    zero = None
    if cache is not None:
        before = cache.snapshot()
        zero_draws = [cache.zero_seed(), cache.zero_seed()]
        after = cache.snapshot()
        assert after == {**before, "zero_bypasses": before["zero_bypasses"] + 2}
        zero = {"before": before, "after": after, "draws": zero_draws}

    save(output, {
        "provider_sha256": sha256(provider), "payload_sha256": sha256(payload),
        "root_oracle_sha256": sha256(oracle), "row_bits": observed,
        "scalar_batch_reorder_subset_chunk_empty_checks": "passed",
        "instrumented": instrumented, "counts_by_step": mechanism, "zero_seed": zero,
        "retained_table_bytes_per_thread": cache.bytes() if cache else None,
    })


@app.command()
def compare(baseline: File, candidate: File, diagnostic: File, output: Output):
    """Compare three separate provider instances down to every diagnostic bit."""
    a, b, c = (json.loads(path.read_text(encoding="utf-8")) for path in (baseline, candidate, diagnostic))
    assert not a["instrumented"] and not b["instrumented"] and c["instrumented"]
    assert len({item["provider_sha256"] for item in (a, b, c)}) == 3
    for key in ("payload_sha256", "root_oracle_sha256", "row_bits", "scalar_batch_reorder_subset_chunk_empty_checks"):
        assert a[key] == b[key] == c[key], key
    assert c["retained_table_bytes_per_thread"] == 458752
    save(output, {"exact_diagnostic_rows": len(a["row_bits"]), "data": c})


@app.command("campaign-counts")
def campaign_counts(
    provider: File, payload: File, runner: File, original: File, histogram: Output, output: Output,
    chunk_size: Annotated[int, typer.Option(min=1)] = 2000,
):
    """Collect live cache counts for the canonical full C production projection."""
    from correctionlib import _core  # noqa: F401  The SDK is needed before opening the DSO.

    cache = Statistics(provider)
    cache.reset()
    old_argv = sys.argv
    old_path = list(sys.path)
    sys.argv = [
        str(runner), "histogram", str(original), str(histogram), "--mode", "full",
        "--provider", str(provider), "--momentum", str(payload), "--selection", str(payload),
        "--efficiency", str(payload), "--chunk-size", str(chunk_size),
    ]
    sys.path.insert(0, str(runner.resolve().parent))
    try:
        runpy.run_path(str(runner), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path

    counts = cache.snapshot()
    calls = counts["hits"] + counts["misses"] + counts["zero_bypasses"]
    assert counts["hits"] > 0 and counts["misses"] > 0
    assert calls == 434913 and counts["zero_bypasses"] == 0, counts
    assert 0 <= counts["overwrites"] <= counts["misses"], counts
    save(output, {
        "calls": calls, "counts": counts, "hit_fraction": counts["hits"] / calls,
        "table_bytes_per_thread": cache.bytes(), "provider_sha256": sha256(provider),
        "payload_sha256": sha256(payload), "histogram_sha256": sha256(histogram), "chunk_size": chunk_size,
    })


if __name__ == "__main__":
    app()
