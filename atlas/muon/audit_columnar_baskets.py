#!/usr/bin/env python3
"""Measure original selected basket ranges and repeated logical requests."""

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import typer

sys.path.insert(0, str(Path(__file__).with_name("columnar")))
from raw_input import MUON_PREFIX, branch_plan, load_workflow, open_source, verify_file
from run_muons import entry_ranges, read_chunk


def inventory(file, tree, fields, step):
    source = file.file.source
    ranges = {}
    records = {}
    for field in fields:
        branch = tree[field]
        for leaf in (branch.branches or [branch]):
            label = field if leaf is branch else f"{field}/{leaf.name}"
            codecs = Counter()
            aligned = []
            repeats = 0
            compressed = 0
            for index in range(leaf.num_baskets):
                first, stop = map(int, leaf.basket_entry_start_stop(index))
                size = int(leaf.basket_compressed_bytes(index))
                compressed += size
                duplicates = ((stop - 1) // step) - (first // step) if stop > first else 0
                repeats += duplicates * size
                if duplicates:
                    aligned.append([first, stop])
                try:
                    key = leaf.basket_key(index)
                except ValueError:
                    codecs["embedded"] += 1
                    continue
                offset = key.data_cursor.index
                if key.data_compressed_bytes == key.data_uncompressed_bytes:
                    codec = "uncompressed"
                else:
                    codec = source.chunk(offset, offset + 2).raw_data.tobytes().decode("ascii", errors="replace")
                codecs[codec] += 1
                ranges[(key.cursor.index, key.cursor.index + size)] = label
            records[label] = {"baskets": leaf.num_baskets, "basket_compression_signatures": dict(codecs),
                              "declared_branch_compression": str(leaf.compression), "compressed_bytes": compressed,
                              "cross_boundary_baskets": aligned, "potential_duplicate_chunk_bytes": repeats}
    return records, ranges


def observe(original, workflow, fields, basket_ranges, step, iterate):
    with open_source(original) as file:
        tree = file["CollectionTree"]
        verify_file(file, tree, workflow["source"])
        source = file.file.source
        physical = Counter()
        original_chunk = source.chunk
        original_chunks = source.chunks

        def counted_chunk(start, stop):
            physical[(int(start), int(stop))] += 1
            return original_chunk(start, stop)

        def counted_chunks(ranges, notifications):
            physical.update((int(start), int(stop)) for start, stop in ranges)
            return original_chunks(ranges, notifications)

        source.chunk = counted_chunk
        source.chunks = counted_chunks
        start = source.num_requested_bytes
        events = 0
        if iterate:
            split = {name for name in fields if tree[name].branches}
            leaves = [name for name in fields if name not in split]
            for data, report in tree.iterate(leaves, step_size=step, library="ak", how=dict, report=True):
                for name in sorted(split):
                    data[name] = tree[name].array(entry_start=report.tree_entry_start,
                        entry_stop=report.tree_entry_stop, library="ak")
                assert set(data) == set(fields)
                events += len(data[MUON_PREFIX + "pt"])
        else:
            for first, last in entry_ranges(tree, 0, None, step):
                data = read_chunk(tree, fields, first, last)
                events += len(data[MUON_PREFIX + "pt"])
        logical = source.num_requested_bytes - start
    assert events == 20_000 and logical == sum((stop - first) * count for (first, stop), count in physical.items())
    repetitions = defaultdict(int)
    once = 0
    duplicate_count = 0
    for (first, stop), requests in physical.items():
        label = basket_ranges.get((first, stop))
        if label is not None:
            once += stop - first
            duplicate_count += requests - 1
            repetitions[label] += (stop - first) * (requests - 1)
    return {"events": events, "read_style": "visible iterate plus separate link reads" if iterate else "explicit separate ranges",
            "requested_logical_bytes": logical, "recorded_ranges": sum(physical.values()),
            "projected_basket_bytes_once": once, "repeat_exact_projected_basket_requests": duplicate_count,
            "repeat_exact_projected_basket_bytes": sum(repetitions.values()),
            "duplicate_bytes_by_branch": {name: count for name, count in sorted(repetitions.items()) if count},
            "other_requested_bytes": logical - once - sum(repetitions.values())}


def main(original: Path, output: Path, step: int = 2000):
    workflow = load_workflow()
    fields = branch_plan(workflow, stored=False, observe=False)
    with open_source(original) as file:
        tree = file["CollectionTree"]
        verify_file(file, tree, workflow["source"])
        inventory_data, ranges = inventory(file, tree, fields, step)
        default_codec = str(file.file.compression)
    document = {
        "source": workflow["source"], "step_events": step, "declared_file_compression": default_codec,
        "basket_signature_explanation": "ROOT block bytes ZL=zlib, ZS=zstd, L4=lz4, XZ=lzma; uncompressed from the basket key",
        "projected_fields": list(fields), "leaf_baskets": inventory_data,
        "projected_compressed_bytes": sum(row["compressed_bytes"] for row in inventory_data.values()),
        "theoretical_repeated_basket_bytes_if_no_reuse": sum(row["potential_duplicate_chunk_bytes"] for row in inventory_data.values()),
        "actual_logical_ranges": [observe(original, workflow, fields, ranges, step, variant) for variant in (False, True)],
        "interpretation": "Logical source requests and compressed basket identity; kernel page-cache or device traffic was not measured.",
    }
    with output.open("x") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    typer.run(main)
