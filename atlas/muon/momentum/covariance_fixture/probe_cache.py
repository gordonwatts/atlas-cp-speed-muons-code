"""Collect cache mechanism counts from a separate full C diagnostic process."""

import ctypes
import hashlib
import json
from pathlib import Path
import runpy
import resource
import sys
from typing import Annotated

import typer

File = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
COUNTERS = (
    "hit_ok", "hit_error", "miss_ok", "miss_error", "eviction", "same_hash_different_key",
    "nonfinite_input_bypass", "nonfinite_weights_bypass", "fp_control_bypass",
)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main(
    provider: File, payload: File, runner: File, original: File, histogram: Path, output: Path,
    chunk_size: Annotated[int, typer.Option(min=1)] = 2000,
):
    from correctionlib import _core  # Load the identical SDK before opening the provider.

    provider = provider.resolve()
    module = ctypes.CDLL(str(provider))
    counts = module.atlas_muon_m05_covariance_counts
    counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    counts.restype = None
    layout = module.atlas_muon_m05_covariance_layout
    layout.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
    layout.restype = None
    reset = module.atlas_muon_m05_covariance_reset
    reset.argtypes = []
    reset.restype = None
    reset()

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

    mappings = Path("/proc/self/maps").read_text(encoding="utf-8").splitlines()
    if not any(line.split(maxsplit=5)[-1] == str(provider) for line in mappings):
        raise RuntimeError("Diagnostic provider mapping missing")
    slots = (ctypes.c_uint64 * (2 * len(COUNTERS)))()
    counts(slots)
    sizes = (ctypes.c_size_t * 4)()
    layout(sizes)
    phases = {}
    for index, name in enumerate(("nominal", "shifted")):
        start = index * len(COUNTERS)
        values = dict(zip(COUNTERS, slots[start:start+len(COUNTERS)], strict=True))
        hits = values["hit_ok"] + values["hit_error"]
        misses = values["miss_ok"] + values["miss_error"]
        bypasses = sum(count for key, count in values.items() if key.endswith("_bypass"))
        requests = hits + misses + bypasses
        if not 0 <= values["eviction"] <= misses:
            raise RuntimeError("Cache replacement count exceeds filled misses")
        phases[name] = {
            "requests": requests, "stored_misses": misses, "bypasses": bypasses,
            "hit_fraction_all_requests": hits / requests if requests else None,
            "counts": values,
        }
    if phases["nominal"]["requests"] == 0:
        raise RuntimeError("No cache calls observed in the full C run")
    data = {
        "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "table_bytes_per_thread": sizes[0], "sets": sizes[1], "ways": sizes[2],
        "diagnostic_counter_bytes_per_thread": sizes[3], "phases": phases, "chunk_size": chunk_size,
        "sha256": {name: digest(path) for name, path in (("provider", provider), ("payload", payload),
                                                        ("runner", runner), ("histogram", histogram))},
    }
    with output.open("x", encoding="utf-8") as target:
        json.dump(data, target, indent=2)
        target.write("\n")


if __name__ == "__main__":
    typer.run(main)
