"""Report real TLeaf layout and logical bytes requested for the bare-pT control."""

import json
from collections import Counter
from pathlib import Path
import sys

import typer

sys.path.insert(0, str(Path(__file__).with_name("columnar")))
from raw_input import branch_plan, load_workflow, open_source, verify_file


def branch_rows(tree, fields):
    owners = {}
    branch_paths = {}
    count_links = []
    count_branches = []

    def describe(branch, name):
        row = {
            "name": branch.name, "class": branch.classname, "typename": branch.typename,
            "entry_offset_table_capacity": branch.member("fEntryOffsetLen", none_if_missing=True),
            "baskets": branch.num_baskets, "declared_compressed_bytes": branch.compressed_bytes,
            "declared_uncompressed_bytes": branch.uncompressed_bytes,
            "basket_compressed_bytes": sum(branch.basket_compressed_bytes(i) for i in range(branch.num_baskets)),
            "basket_uncompressed_bytes": sum(branch.basket_uncompressed_bytes(i) for i in range(branch.num_baskets)),
            "leaves": [], "children": {}, "branch_element_count_references": {},
        }
        branch_paths[id(branch)] = name
        for member in ("fBranchCount", "fBranchCount2"):
            target = branch.member(member, none_if_missing=True)
            if target is not None:
                count_branches.append((name, row["branch_element_count_references"], member, target))
        for leaf in branch.member("fLeaves"):
            count = leaf.member("fLeafCount", none_if_missing=True)
            leaf_row = {
                "name": leaf.member("fName"), "title": leaf.member("fTitle"), "class": leaf.classname,
                "fLen": leaf.member("fLen"), "fLenType": leaf.member("fLenType"), "fOffset": leaf.member("fOffset"),
                "count_leaf": None,
            }
            owners[id(leaf)] = name
            if count is not None:
                count_links.append((name, leaf_row, count))
            row["leaves"].append(leaf_row)
        for child in branch.branches:
            row["children"][child.name] = describe(child, f"{name}/{child.name}")
        return row

    selected = {field: describe(tree[field], field) for field in fields}
    dependencies = {}
    unresolved_leaves = {id(count) for _, _, count in count_links if id(count) not in owners}
    unresolved_branches = {id(count) for _, _, _, count in count_branches if id(count) not in branch_paths}
    if unresolved_leaves or unresolved_branches:
        for path, branch in tree.iteritems(recursive=True, full_paths=True):
            if id(branch) in unresolved_branches or any(id(leaf) in unresolved_leaves for leaf in branch.member("fLeaves")):
                dependencies[path] = describe(branch, path)
    for name, row, count in count_links:
        row["count_leaf"] = {"branch_path": owners.get(id(count)), "name": count.member("fName"),
                             "separate_branch": owners.get(id(count)) != name}
    if any(row["count_leaf"]["branch_path"] is None for _, row, _ in count_links):
        raise RuntimeError("An explicit fLeafCount points outside the resolved branch tree")
    for name, row, member, count in count_branches:
        resolved = branch_paths.get(id(count))
        if resolved is None:
            raise RuntimeError(f"Unresolved {member} for {name}")
        row[member] = {"branch_path": resolved, "baskets": count.num_baskets,
                       "separate_branch": resolved != name}
    return selected, dependencies


def summarize(branches):
    def terminals(row):
        if not row["children"]:
            yield row
        for child in row["children"].values():
            yield from terminals(child)

    rows = [leaf for row in branches.values() for leaf in terminals(row)]
    return {"top_level_fields": len(branches), "terminal_branches": len(rows),
            "terminal_tleaves": sum(len(row["leaves"]) for row in rows),
            "terminal_count_leaf_links": sum(leaf["count_leaf"] is not None
                                              for row in rows for leaf in row["leaves"]),
            "terminal_branch_element_count_links": sum(len(row["branch_element_count_references"]) for row in rows),
            **{field: sum(row[field] for row in rows) for field in (
                "baskets", "declared_compressed_bytes", "declared_uncompressed_bytes",
                "basket_compressed_bytes", "basket_uncompressed_bytes",
            )}}


def link_requests(original, workflow, ranges, parents):
    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        reader = source.file.source
        chunk, chunks = reader.chunk, reader.chunks
        physical = Counter()

        def record_chunk(first, last):
            physical[(int(first), int(last))] += 1
            return chunk(first, last)

        def record_chunks(requests, notifications):
            physical.update((int(first), int(last)) for first, last in requests)
            return chunks(requests, notifications)

        reader.chunk, reader.chunks = record_chunk, record_chunks
        start = reader.num_requested_bytes
        for first in range(0, tree.num_entries, 2000):
            last = min(first + 2000, tree.num_entries)
            for field in parents:
                arrays = tree[field].array(entry_start=first, entry_stop=last, library="ak")
                if len(arrays) != last - first:
                    raise RuntimeError(f"Incomplete list of links for {field}")
        logical = reader.num_requested_bytes - start
    if logical != sum((last - first) * count for (first, last), count in physical.items()):
        raise RuntimeError("Physical request audit does not sum to the logical source counter")
    matched = {}
    for kind, records in ranges.items():
        matched[kind] = {
            "physical_baskets_available": len(records),
            "exact_range_requests": sum(physical[key] for key in records),
            "exact_range_requested_bytes": sum((last - first) * physical[(first, last)] for first, last in records),
        }
    return {"logical_requested_after_tree_open": logical, **matched,
            "other_requests_bytes": logical - sum(row["exact_range_requested_bytes"] for row in matched.values())}


def main(original: Path, output: Path):
    workflow = load_workflow()
    full_fields = branch_plan(workflow, stored=False, observe=False)
    stored_fields = branch_plan(workflow, stored=True)
    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        full, full_dependencies = branch_rows(tree, full_fields)
        stored, stored_dependencies = branch_rows(tree, stored_fields)
        link_parents = {name: row for name, row in full.items() if row["children"]}
        count_fields = [tree[name] for name in link_parents]
        child_fields = [child for field in count_fields for child in field.branches]
        link_ranges = {}
        for kind, branches in (("link_parents_with_count_leaves", count_fields), ("terminal_link_children", child_fields)):
            records = set()
            for branch in branches:
                for index in range(branch.num_baskets):
                    start = int(branch.basket_key(index).cursor.index)
                    records.add((start, start + int(branch.basket_compressed_bytes(index))))
            link_ranges[kind] = records
        tree_identity = {"name": tree.name, "class": tree.classname, "entries": tree.num_entries,
                         "total_declared_compressed_bytes": tree.member("fZipBytes"),
                         "total_declared_uncompressed_bytes": tree.member("fTotBytes")}
    # Exercise only the stored input projection, without any analysis or histogram filling.
    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        reader = source.file.source
        bootstrap = reader.num_requested_bytes
        events = sum(len(arrays[stored_fields[0]]) for arrays in tree.iterate(stored_fields, step_size=2000,
                                                                            library="ak", how=dict))
        if events != tree.num_entries:
            raise RuntimeError("The stored branch sample did not traverse the whole input")
        stored_logical = {"tree_and_file_bootstrap": bootstrap, "projection": reader.num_requested_bytes - bootstrap,
                          "through_last_chunk": reader.num_requested_bytes, "events": events}
    report = {"source": workflow["source"], "tree": tree_identity,
              "full": {"totals": summarize(full), "branches": full, "extra_count_branches": full_dependencies,
                       "link_only_logical_requests": link_requests(original, workflow, link_ranges, link_parents),
                       "split_link_parent_totals": {key: sum(row[key] for row in link_parents.values()) for key in (
                           "baskets", "basket_compressed_bytes", "basket_uncompressed_bytes",
                       )}},
              "stored": {"totals": summarize(stored), "branches": stored, "extra_count_branches": stored_dependencies,
                         "logical_bytes_requested": stored_logical}}
    with output.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    typer.run(main)
