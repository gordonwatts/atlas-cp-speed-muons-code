"""Prepare the exact original-input state schedule and summarize M12 diagnostic pairs."""

import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Annotated

import awkward as ak
import typer

app = typer.Typer(no_args_is_help=True)
File = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Output = Annotated[Path, typer.Argument(dir_okay=False)]


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(output, result):
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")


@app.command()
def fixture(original: File, driver: File, batches: Output, receipt: Output):
    """Read only original AnalysisMuons multiplicities to recreate the momentum batch order."""
    sys.path.insert(0, str(driver.resolve().parent))
    from muon_analysis import StatePlan
    from raw_input import branch_plan, check_branches, load_workflow, open_source, verify_file
    from run_muons import entry_ranges

    workflow = load_workflow()
    plan = StatePlan.build(workflow, full=True)
    states = list(dict.fromkeys(plan.kinematics_for(state) for state in plan.states))
    branch, = branch_plan(workflow, stored=True)
    chunks = []
    with open_source(original) as source:
        tree = source["CollectionTree"]
        verify_file(source, tree, workflow["source"])
        check_branches(tree, (branch,), workflow["schema"])
        for first, last in entry_ranges(tree, 0, None, 2000):
            raw_pt = tree[branch].array(entry_start=first, entry_stop=last, library="ak")
            assert len(raw_pt) == last - first
            chunks.append((first, last, int(ak.sum(ak.num(raw_pt, axis=1)))))
    assert len(states) == 29 and states[0] == "NOSYS"
    assert len(chunks) == 10 and sum(row[2] for row in chunks) == 14997
    with batches.open("x", encoding="utf-8") as handle:
        for first, last, rows in chunks:
            for state in states:
                handle.write(f"{first}\t{last}\t{rows}\t{state}\n")
    save(receipt, {
        "input": str(original.resolve()), "source_uuid": workflow["source"]["uuid"],
        "declared_source_sha256": workflow["source"]["sha256"], "branch": branch,
        "ranges_and_muon_counts": chunks, "kinematic_states": states,
        "batches": 290, "rows_per_full_pass": 434913, "fixture_sha256": digest(batches),
        "source_sha256": {name: digest(driver.with_name(name)) for name in (
            driver.name, "raw_input.py", "muon_analysis.py", "workflow.json",
        )},
    })


def paired_deltas(pairs, clock):
    values = [
        (row["named"][clock] - row["bound"][clock]) / row["named"]["repetitions"]
        for row in pairs
    ]
    mean = statistics.mean(values)
    deviation = statistics.stdev(values)
    # Five retained pairs, so four Student-t degrees of freedom.
    half_width = 2.7764451051977987 * deviation / math.sqrt(len(values))
    return {
        "per_full_pass_deltas_seconds": values, "mean_seconds": mean,
        "sample_sd_seconds": deviation, "student_95_interval_seconds": [mean - half_width, mean + half_width],
    }


@app.command()
def summarize(records: File, output: Output):
    """Summarize five interleaved pairs using the declared 13.45 ms ranking threshold."""
    with records.open(newline="", encoding="utf-8") as handle:
        result = [{
            "pair": int(row["pair"]), "warmup": int(row["warmup"]), "mode": row["mode"],
            "repetitions": int(row["repetitions"]), "rows": int(row["rows"]), "checksum": int(row["checksum"]),
            "wall_seconds": float(row["wall_seconds"]), "cpu_seconds": float(row["cpu_seconds"]),
        } for row in csv.DictReader(handle, delimiter="\t")]
    assert len(result) == 12 and len({row["checksum"] for row in result}) == 1
    pairs = []
    for index in range(6):
        two = result[2 * index:2 * index + 2]
        assert [row["mode"] for row in two] == (["named", "bound"] if index % 2 == 0 else ["bound", "named"])
        for row in two:
            assert row["pair"] == index and row["warmup"] == int(index == 0)
            assert row["repetitions"] == 32 and row["rows"] == 434913 * row["repetitions"]
            assert all(math.isfinite(row[clock]) and row[clock] > 0 for clock in ("wall_seconds", "cpu_seconds"))
        pairs.append({row["mode"]: row for row in two})
    threshold = 2.69 * 0.005
    statistics_by_clock = {clock: paired_deltas(pairs[1:], clock) for clock in ("wall_seconds", "cpu_seconds")}
    passes = all(values["student_95_interval_seconds"][0] > threshold for values in statistics_by_clock.values())
    save(output, {
        "measurement": "diagnostic named state map lookup versus per-batch bound map lookup",
        "original_complete_C_time_seconds_for_ranking": 2.69, "ranking_threshold_seconds_per_full_pass": threshold,
        "one_warmup_pair_excluded": True, "retained_pairs": len(pairs) - 1,
        "rows_per_full_pass": 434913, "candidate_bound_states_per_full_pass": 290,
        "raw_records_sha256": digest(records), "raw_ordered_records": result,
        "named_minus_bound": statistics_by_clock,
        "ranking": "consider one implementation trial" if passes else "defer batch-binding architecture",
    })


if __name__ == "__main__":
    app()
