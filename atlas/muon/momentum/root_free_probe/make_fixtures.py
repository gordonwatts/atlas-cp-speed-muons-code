"""Build exact axis and positive-seed boundary fixtures from campaign artifacts."""

import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated

import typer

File = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Output = Annotated[Path, typer.Argument(dir_okay=False)]
MAX_SEED = (1 << 32) - 1


def record_seeds(path):
    with path.open(encoding="utf-8") as source:
        first = source.readline()
        source.seek(0)
        if "root_seed" in first.split():
            records = csv.DictReader(source, delimiter="\t")
            values = [int(row["root_seed"]) for row in records if row.get("state", "NOSYS") == "NOSYS"]
        else:
            values = [int(line) for line in source if line.strip()]
    if not values or any(not 1 <= value <= MAX_SEED for value in values):
        raise ValueError("Seed fixture must contain positive representable UInt32 values")
    return values


def maps(year):
    for detector in year["scale"]:
        yield from detector
    yield from year["expected"]
    for detector in year["sagitta"]:
        yield from (hist for hist in detector if hist is not None)


def record_axes(payload):
    document = json.loads(payload.read_bytes())
    momentum, = [definition for definition in document["corrections"] if definition["name"] == "muon_momentum"]
    config = momentum["data"]["config"]
    if document["schema_version"] != 3 or config["rng"] != "ROOT-6.40.02-TRandom3-Gaus":
        raise ValueError("Feed the frozen original-stream momentum payload")
    result = {}
    for year in config["years"]:
        for index, hist in enumerate(maps(year)):
            for coordinate, angle_axis in zip(("eta", "phi"), hist[:2], strict=True):
                bins, lower, upper, edges = angle_axis
                key = (bins, lower, upper, tuple(edges))
                if bins < 1 or (edges and len(edges) != bins + 1):
                    raise ValueError("Invalid momentum calibration axis")
                if not all(re.fullmatch(r"[0-9a-f]{16}", value) for value in (lower, upper, *edges)):
                    raise ValueError("Expected exact binary64 field encoding")
                result.setdefault(key, []).append(f"{year['name']}/{index}/{coordinate}")
    return result


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main(payload: File, source_seeds: File, axes: Output, seeds: Output, provenance: Output):
    """Copy source seeds and all unique accepted map axes without floating-point conversion."""
    unique_axes = record_axes(payload)
    source_values = record_seeds(source_seeds)
    with axes.open("x", encoding="utf-8", newline="\n") as output:
        for i, (bins, lower, upper, edges) in enumerate(unique_axes):
            print(f"payload-{i}", bins, lower, upper, len(edges), *edges, file=output)
    with seeds.open("x", encoding="utf-8", newline="\n") as output:
        for seed in sorted(set(source_values)):
            print(seed, file=output)
    with provenance.open("x", encoding="utf-8", newline="\n") as output:
        json.dump({
            "payload_sha256": digest(payload), "source_seeds_sha256": digest(source_seeds),
            "axis_fixture_sha256": digest(axes), "seed_fixture_sha256": digest(seeds),
            "source_seed_rows": len(source_values), "unique_seeds": len(set(source_values)),
            "minimum_seed": min(source_values), "maximum_seed": max(source_values),
            "axes": {f"payload-{i}": records for i, records in enumerate(unique_axes.values())},
        }, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    typer.run(main)
