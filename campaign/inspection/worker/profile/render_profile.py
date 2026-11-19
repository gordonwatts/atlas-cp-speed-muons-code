#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["typer"]
# ///
"""Render the final native profile evidence as a report-ready Markdown section."""

from collections import defaultdict
import csv
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote


@dataclass(frozen=True)
class Symbol:
    dso: str
    name: str
    category: str
    resolved: bool
    samples: int


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def symbol_rows(path, total=None):
    source = csv_rows(path)
    if total is None:
        total = sum(int(row["samples"]) for row in source)
    if total <= 0:
        raise ValueError(f"{path.name} must have a positive sample denominator")
    rows = []
    seen = set()
    for row in source:
        key = row["dso"], row["symbol"]
        count = int(row["samples"])
        if (key in seen or row["resolved"] not in {"True", "False"} or not 0 < count <= total
                or not math.isclose(float(row["sample_share_percent"]), 100 * count / total, abs_tol=1e-8)):
            raise ValueError(f"Inconsistent symbol count or classification in {path.name} for {key}")
        seen.add(key)
        rows.append(Symbol(*key, row["category"], row["resolved"] == "True", count))
    return sorted(rows, key=lambda row: (-row.samples, row.dso, row.name))


def partition_exclusive(rows, top):
    resolved = [row for row in rows if row.resolved]
    unresolved, remainder = defaultdict(list), defaultdict(list)
    for row in rows:
        if not row.resolved:
            unresolved[row.dso].append(row)
    for row in resolved[top:]:
        remainder[row.category, row.dso].append(row)
    return resolved[:top], unresolved, remainder


def samples(rows):
    return sum(row.samples for row in rows)


def table(headers, rows):
    def line(cells):
        return "| " + " | ".join(str(cell).replace("|", r"\|") for cell in cells) + " |"

    return "\n".join([line(headers), line(["---"] * len(headers)), *(line(row) for row in rows)])


def code(text):
    return f"`{text.replace(chr(96), chr(39))}`"


def link(path, label, base):
    try:
        relative = Path(os.path.relpath(path.resolve(), base)).as_posix()
    except ValueError:
        relative = path.resolve().as_posix()
    return f"[{label}]({quote(relative, safe='/.:_-')})"


def dso_labels(dsos):
    by_name = defaultdict(set)
    for dso in dsos:
        by_name[PurePosixPath(dso).name or "[unknown]"].add(dso)
    return {dso: ("Unreported" if dso in {"", "[unknown]", "unknown"} else
                  code(name if len(paths) == 1 else dso))
            for name, paths in by_name.items() for dso in paths}


OPERATIONS = (
    ("ShallowAuxContainer::remakeAuxIDs", "xAOD shallow auxiliary ID rebuild"),
    ("ConcurrentBitset::Impl::count", "Concurrent bit set population count"),
    ("FullPivLU", "Eigen full-pivot LU"),
    ("triangular_solve", "Eigen triangular solve"),
    ("_solve_impl", "Eigen factorized solve"),
    ("gebp_kernel", "Eigen blocked matrix multiplication"),
    ("gemm_pack", "Eigen matrix multiplication packing"),
    ("TAxis::FindFixBin", "ROOT axis bin lookup"),
    ("Axis::coordinate_bin", "Muon calibration axis bin lookup"),
    ("BatchInput::at", "CorrectionLib batch argument access"),
    ("expected_resolution", "Expected detector resolution"),
    ("std::__detail::__variant", "C++ variant dispatch"),
    ("MomentumNode::", "Momentum correction"),
    ("AfterCalibration::", "Post-calibration muon selection"),
    ("ToolRoute::", "Efficiency route selection"),
    ("MapResult::", "Efficiency map result"),
    ("PluginNode::evaluate_batch", "CorrectionLib plug-in batch evaluation"),
    ("Histogram::lookup", "Histogram lookup"),
    ("TRandom", "ROOT random generator"),
    ("_PyEval_EvalFrame", "CPython frame evaluation"),
    ("memcmp", "Memory comparison"),
    ("memcpy", "Memory copy"),
    ("memmove", "Memory move"),
    ("malloc", "Memory allocation"),
    ("realloc", "Memory reallocation"),
    ("SystematicVariation::", "CP systematic variation operation"),
    ("SystematicSet::", "CP systematic set operation"),
)


def operation(name):
    if "FullPivLU" in name and (size := re.search(r"Eigen::Matrix<[^,]+, (\d+), (\d+),", name)):
        return f"Eigen full-pivot LU, {size[1]}×{size[2]} matrix"
    return next((label for symbol, label in OPERATIONS if symbol in name), "Stage unassigned")


def compact(name, width=92):
    """Shorten template arguments, preserving an exact symbol in the linked CSV."""
    name = name.replace("(anonymous namespace)::", "").split(" [clone ", 1)[0]
    depth = 0
    text = []
    for index, character in enumerate(name):
        if character == "<" and "operator" not in name[max(0, index - 9):index]:
            if depth == 0:
                text.append("<…>")
            depth += 1
        elif character == ">" and depth:
            depth -= 1
        elif depth == 0:
            text.append(character)
    short = "".join(text)
    return short if len(short) <= width else short[:width - 1] + "…"


def count_share(count, total):
    return f"{count:,}", f"{100 * count / total:.2f}%"


def exclusive_tables(rows, total, labels, top, source_link):
    named, unresolved, remainder = partition_exclusive(rows, top)
    named_count = samples(named)
    unresolved_count = sum(samples(group) for group in unresolved.values())
    remaining_count = sum(samples(group) for group in remainder.values())
    if named_count + remaining_count + unresolved_count != total:
        raise ValueError("The exclusive tables do not reconcile to the captured denominator")
    named_table = table(("Native routine", "DSO", "Operation", "Samples", "Share"), [
        (code(compact(row.name)), labels[row.dso], operation(row.name), *count_share(row.samples, total))
        for row in named
    ])
    remainder_table = table(("Group", "DSO", "Examples", "Samples", "Share"), [
        (category, labels[dso], ", ".join(code(compact(row.name, 58)) for row in group[:2]),
         *count_share(samples(group), total))
        for (category, dso), group in sorted(remainder.items(), key=lambda item: (-samples(item[1]), item[0]))
    ])
    unresolved_table = table(("Reported DSO", "Unresolved leaves", "Share"), [
        (labels[dso], *count_share(samples(group), total))
        for dso, group in sorted(unresolved.items(), key=lambda item: (-samples(item[1]), item[0]))
    ])
    return "\n\n".join((
        "#### Leading exclusive costs", named_table,
        "#### Remaining resolved leaves", remainder_table if remainder else "No additional resolved leaves.",
        "#### Unresolved leaves", unresolved_table if unresolved else "Every captured leaf had a resolved symbol.",
        f"The named rows account for **{named_count:,}** samples, the remaining resolved rows for "
        f"**{remaining_count:,}**, and unresolved leaves for **{unresolved_count:,}**. "
        f"Together they account for all **{total:,}** captured samples. Percentages are rounded independently. {source_link}",
    ))


def parse_stacks(path, total):
    stacks = []
    for row in csv_rows(path):
        frames = []
        for frame in row["leaf_to_root"].split(" → "):
            match = re.fullmatch(r"(.*) \(([^()]*(?: \(deleted\))?)\)", frame)
            if match is None:
                raise ValueError(f"Malformed stack frame in {path.name}")
            frames.append(match.groups())
        stacks.append((int(row["samples"]), frames))
    if sum(count for count, _ in stacks) != total or any(count <= 0 for count, _ in stacks):
        raise ValueError("Exact stack counts do not reconcile")
    return sorted(stacks, key=lambda item: -item[0])


ANCHORS = (
    lambda row: "MomentumNode::" in row.name,
    lambda row: "AfterCalibration::" in row.name,
    lambda row: any(text in row.name for text in ("ToolRoute::", "MapResult::")),
    lambda row: row.category == "CorrectionLib evaluator and bindings",
    lambda row: row.category == "Awkward and uproot native libraries",
    lambda row: row.category == "Compression libraries",
    lambda row: row.category == "Histogram storage",
)


def inclusive_table(rows, stacks, total, labels, limit):
    resolved = [row for row in rows if row.resolved]
    anchors = dict.fromkeys(row for match in ANCHORS if (row := next((r for r in resolved if match(r)), None)))
    anchors.update(dict.fromkeys(resolved))
    result = []
    for row in list(anchors)[:limit]:
        frame = (row.name, row.dso)
        matches = [(count, stack) for count, stack in stacks if frame in stack]
        if sum(count for count, _ in matches) != row.samples:
            raise ValueError(f"Inclusive count does not match exact stack observations for {row.name}")
        _, stack = matches[0]
        index = stack.index(frame)
        segment = stack[max(0, index - 2):index + 2]
        example = " → ".join(code(compact(symbol, 52)) for symbol, _ in reversed(segment))
        result.append((code(compact(row.name)), labels[row.dso], example, *count_share(row.samples, total)))
    return table(("Inclusive anchor", "Anchor DSO", "Example captured caller path", "Samples", "Share"), result)


def run_scope(evidence, base, summary):
    sampled, control, profile = (evidence[key] for key in ("sampled", "control", "profile"))
    repeated = evidence["repeat"]
    minimum = max(60, evidence["minimum_processing_seconds"])
    if (not evidence["minimum_met"] or sampled["input_processing_wall_seconds"] < minimum
            or any(len(run["passes"]) != repeated or not run["accepted_histogram_array_equal"]
                   for run in (sampled, control))):
        raise ValueError("The final block requires matched repetitions, histogram equality and at least 60 s processing")
    placement = sampled["placement"]
    owners = csv_rows(summary / "sample-ownership.csv")
    owned = [row for row in owners if int(row["pid"]) == placement["pid"]]
    total = profile["samples"]
    if (sum(int(row["samples"]) for row in owners) != total
            or sum(int(row["samples"]) for row in owned) != profile["python_owner_samples"]
            or {int(row["tid"]) for row in owned} != set(profile["python_owner_tids"])
            or {int(row["cpu"]) for row in owned} != set(profile["python_owner_cpu"])):
        raise ValueError("The trace ownership table disagrees with the profile receipt")
    other = total - profile["python_owner_samples"]
    _, owner_share = count_share(profile["python_owner_samples"], total)
    fields = ("measured_wall_seconds", "input_processing_wall_seconds", "input_processing_cpu_seconds")
    timing_rows = [
        (name, f"{control[key]:.3f}", f"{sampled[key]:.3f}", f"{evidence['sampled_to_control_ratios'][key]:.3f}×")
        for name, key in zip(("Command wall", "Input processing wall", "Input processing CPU"), fields, strict=True)
    ]
    control_outside, sampled_outside = (run["outside_pass_wall_seconds"] for run in (control, sampled))
    timing_rows.append(("Wall outside passes", f"{control_outside:.3f}", f"{sampled_outside:.3f}",
                        f"{sampled_outside / control_outside:.3f}×" if control_outside > 0 else "—"))
    timings = table(("Timing boundary", "Control (s)", "Sampled (s)", "Sampled / control"), timing_rows)
    receipt = link(summary / "evidence.json", "Run scope, per-pass times and exact histogram check", base)
    ownership = link(summary / "sample-ownership.csv", "Sample ownership", base)
    collector = f"The collector used CPU {evidence['collector_cpu']} during analysis."
    initial = placement.get("perf_collector_startup_other_affinity", [])
    if initial:
        cpus = ", ".join(sorted({str(row["affinity"]) for row in initial}))
        collector += f" Before the analysis process appeared, the collector was also observed on CPU {cpus}."
    return "\n\n".join((
        "#### Scope and sampling overhead",
        f"The standalone full-systematics workflow reopened the original file with `-n {repeated}`. "
        f"Each pass covered **{sampled['unique_events']:,} events** and **{sampled['unique_muons']:,} muons** across "
        f"97 states. Each run accounted for {sampled['event_visits']:,} event visits and "
        f"{sampled['muon_visits']:,} muon visits. The sampled passes covered "
        f"**{sampled['input_processing_wall_seconds']:.3f} s** of input processing. Both runs reproduced the "
        f"accepted histogram arrays exactly. Cache warmth can carry across passes. {receipt}",
        f"The analysis used CPU {evidence['analysis_cpu']} and one thread. {collector} Python PID {placement['pid']} owned "
        f"**{profile['python_owner_samples']:,}** samples ({owner_share}), with {other:,} in the other launched "
        f"processes. {ownership}",
        timings,
        "Command wall time covers launch through exit. Each pass covers original-file open through the final "
        f"fill and source close. The matched runs quantify collection overhead for this repeated workload. {receipt}",
    ))


def recorded_files(summary, base):
    files = (
        ("sampled", "command.txt", "Sampled command"), ("control", "command.txt", "Control command"),
        ("probe", "counter-support.json", "Counter probe"), ("sampled", "perf.data", "Raw perf data"),
        ("sampled", "perf.script", "Decoded trace"), ("sampled", "perf-build-ids.txt", "Perf build IDs"),
        ("sampled", "perf-event-stats.txt", "Perf record statistics"), ("sampled", "runtime.json", "Loaded runtime"),
    )
    result = []
    for folder, filename, label in files:
        options = (summary.parent / folder / filename, summary / folder / filename,
                   summary / f"{folder}-{filename}", summary / filename)
        if path := next((path for path in options if path.is_file()), None):
            result.append(link(path, label, base))
    result.append(link(summary / "evidence.json", "Recorded artifact paths and hashes", base))
    return " · ".join(result)


def capture_quality(evidence, summary, base):
    profile = evidence["profile"]
    denominator = profile["samples"]
    multi, multi_share = count_share(profile["stacks_with_multiple_frames"], denominator)
    unknown, unknown_share = count_share(profile["unresolved_leaf_samples"], denominator)
    lost = profile["lost_samples"]
    loss_kinds = ", ".join(f"`{kind}` {count:,}" for kind, count in profile["lost_by_record_type"].items())
    losses = f"{lost:,} recorded lost samples" if lost is not None else (
        f"undetermined lost count ({len(profile['unparsed_loss_records']):,} unparsed loss records)"
    )
    if loss_kinds:
        losses += f" ({loss_kinds})"
    reported = profile["perf_reported_samples"]
    if reported is not None and reported != denominator:
        raise ValueError("The perf-reported count disagrees with the captured sample denominator")
    counters = (evidence["counter_support"] or {}).get("hardware", {}).get("events", {})
    monitored = ", ".join(
        f"`{name}` returned {row['returned_value']:g}" if row.get("supported") else
        f"`{name}` unavailable (`{row.get('native_token', 'No counter row')}`)"
        for name, row in counters.items()
    )
    counter_text = f" The hardware capability probe reported {monitored}." if monitored else ""
    version = f" with `{evidence['perf_version']}`" if evidence["perf_version"] else ""
    runtime = evidence["sampled"].get("native_runtime")
    runtime_text = ""
    if runtime:
        provider = runtime["provider"]
        runtime_text = (f" The loaded provider was {code(PurePosixPath(provider['path']).name)} "
                        f"(SHA-256 prefix {code(provider['sha256'][:12])}).")
    return ("#### Capture quality and provenance\n\n"
            f"Sampling used `{profile['event']}`, a requested {profile['requested_hz']} Hz and "
            f"{profile['call_graph'].upper()} call stacks{version}. The trace had {losses}. "
            f"It contains **{unknown} unresolved leaves** ({unknown_share}) and "
            f"**{multi} samples with multiple frames** ({multi_share}).{counter_text}{runtime_text} "
            f"{recorded_files(summary, base)}")


def render(summary, base, top=8, inclusive=8, title="Final native CPU profile"):
    evidence = json.loads((summary / "evidence.json").read_text(encoding="utf-8-sig"))
    if not evidence.get("sampled") or not evidence.get("profile"):
        raise ValueError("The evidence directory does not contain a completed native sample capture")
    profile = evidence["profile"]
    total = profile["samples"]
    exclusive_path = summary / "exclusive-symbols.csv"
    exclusive = symbol_rows(exclusive_path, total)
    inclusive_path = summary / "inclusive-symbols.csv"
    inclusive_rows = symbol_rows(inclusive_path, total)
    stack_path = summary / "inclusive-stacks.csv"
    stacks = parse_stacks(stack_path, total)
    labels = dso_labels({row.dso for row in exclusive + inclusive_rows})
    if samples(exclusive) != total or samples(row for row in exclusive if not row.resolved) != profile["unresolved_leaf_samples"]:
        raise ValueError("Exclusive symbol shares disagree with the recorded sample totals")
    phase_text = (
        "The profile did not assign individual samples to analysis stages. Shared interpreter, allocation "
        "and copying routines can appear in several stages. Use separate elapsed-time scopes to discuss stage costs."
        if not profile["sample_phase_assignment_performed"] else
        "The linked evidence records stage assignment separately from these symbol counts."
    )
    return "\n\n".join((
        f"### {title}",
        f"The profile covers the launched process tree in user mode, including startup and final output. "
        f"Sampled CPU shares use all **{total:,}** captured observations. Self (exclusive) cost counts the "
        "routine executing at each sampled leaf. Inclusive cost also counts callees through the captured stack. "
        "These shares describe CPU observations. Elapsed time and process placement are reported separately.",
        exclusive_tables(exclusive, total, labels, top, link(exclusive_path, "Complete exclusive symbols", base)),
        "#### Inclusive observations",
        inclusive_table(inclusive_rows, stacks, total, labels, inclusive),
        "Inclusive rows can share observations because one stack can contain several anchors. Each example is a "
        f"captured caller-to-callee excerpt. {phase_text} "
        f"{link(inclusive_path, 'Inclusive symbols', base)} · {link(stack_path, 'Exact captured stacks', base)}",
        run_scope(evidence, base, summary),
        capture_quality(evidence, summary, base),
    )) + "\n"


def main(
    summary: Path, output: Path, link_from: Path | None = None,
    top: int = 8, inclusive: int = 8, title: str = "Final native CPU profile",
):
    """Render from a copied profile_evidence.py summary, with links relative to the report directory."""
    if top < 1 or inclusive < 1:
        raise ValueError("Both table limits must be positive")
    base = link_from or output.parent
    if not base.is_dir():
        raise ValueError("The report link base must be an existing directory")
    markdown = render(summary.resolve(), base.resolve(), top, inclusive, title)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(markdown)


if __name__ == "__main__":
    import typer

    typer.run(main)
