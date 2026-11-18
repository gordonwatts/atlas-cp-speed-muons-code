#!/usr/bin/env python3
"""Collect process placement and summarize a pinned muon native profile."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from hashlib import file_digest, sha256
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Annotated

import typer


app = typer.Typer(no_args_is_help=True)
ANALYSIS_CPU = 18
EVENT = "cpu-clock:u"
TIME = re.compile(r"wall=(\S+) user=(\S+) system=(\S+) exit=(\d+)")
SAMPLE = re.compile(
    r"^\s*(?P<comm>.*?)\s+(?P<pid>\d+)(?:\s*/\s*(?P<tid>\d+))?\s+"
    r"\[(?P<cpu>\d+)\]\s+(?P<time>\d+(?:\.\d+)?):\s+cpu-clock:u:\s*(?P<leaf>.*)$"
)
FRAME = re.compile(r"^\s*(?:0x)?[\da-fA-F]+\s+(.*?)\s*\(([^()]+(?: \(deleted\))?)\)\s*$")
OFFSET = re.compile(r"\+0x[\da-fA-F]+$")
UNKNOWN = {"", "[unknown]", "unknown", "[unresolved]"}
LOSS = re.compile(r"PERF_RECORD_(LOST(?:_SAMPLES)?).*?\blost\s*[:=]?\s*(\d+)", re.IGNORECASE)


def json_new(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")


def file_identity(path):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        return {"path": str(path), "sha256": file_digest(stream, "sha256").hexdigest(), "bytes": path.stat().st_size}


def descendants(pid):
    """Use each task's children so launcher worker threads can create the Python child."""
    pending = [pid]
    visited = set()
    while pending:
        child = pending.pop()
        if child in visited:
            continue
        visited.add(child)
        base = Path("/proc") / str(child)
        yield base
        for path in (base / "task").glob("*/children"):
            try:
                pending.extend(map(int, path.read_text().split()))
            except (FileNotFoundError, ProcessLookupError):
                continue


def observed_process(base):
    try:
        data = dict(line.split(":", 1) for line in (base / "status").read_text().splitlines() if ":" in line)
        if data["State"].lstrip().startswith("Z"):
            return None
        argv = [os.fsdecode(item) for item in (base / "cmdline").read_bytes().split(b"\0") if item]
        binary = os.readlink(base / "exe")
        return {
            "pid": int(base.name), "ppid": int(data["PPid"]), "name": data["Name"].strip(),
            "affinity": data["Cpus_allowed_list"].strip(), "threads": int(data["Threads"]),
            "executable": binary, "cwd": os.readlink(base / "cwd"), "argv": argv,
        }
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


@app.command()
def observe(pid: int, output: Path):
    """Record placement and executable identity for a command's descendants."""
    root = Path("/proc") / str(pid)
    with output.open("x", encoding="utf-8") as stream:
        while root.is_dir():
            snapshot = [value for process in descendants(pid) if (value := observed_process(process))]
            if not snapshot:
                break
            stream.write(json.dumps({"wall_time_unix_ns": time.time_ns(), "processes": snapshot}) + "\n")
            stream.flush()
            time.sleep(0.5)


def stat_probe(directory, group, expected):
    csv_path = directory / f"{group}.csv"
    status = int((directory / f"{group}.exit").read_text())
    rows = {}
    if csv_path.exists():
        for line in csv_path.read_text().splitlines():
            fields = line.split(";")
            if len(fields) < 3 or fields[2].strip() not in expected:
                continue
            token = fields[0].strip()
            try:
                value = float(token)
            except ValueError:
                value = None
            supported = status == 0 and value is not None and math.isfinite(value) and value >= 0
            rows[fields[2].strip()] = {
                "supported": supported, "returned_value": value if supported else None,
                "native_token": token, "unit": fields[1].strip() or None, "raw_row": line,
            }
    return {
        "exit": status, "events": {event: rows.get(event, {"supported": False, "native_token": "No counter row"})
                                   for event in expected},
        "stderr": (directory / f"{group}.stderr").read_text(errors="replace").strip(),
    }


@app.command()
def counter_support(probe: Path):
    """Decide software availability and record actual returned hardware values."""
    software = stat_probe(probe, "software", (EVENT,))
    hardware = stat_probe(probe, "hardware", ("cycles:u", "instructions:u"))
    json_new(probe / "counter-support.json", {"software": software, "hardware": hardware})
    if not software["events"][EVENT]["supported"]:
        raise typer.Exit(1)


def read_workload_stdout(path):
    for line in reversed(path.read_text(errors="replace").splitlines()):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and isinstance(item.get("passes"), list):
            return item
    raise ValueError(f"No valid repeat-pass summary in {path}")


@app.command()
def estimate_passes(
    timings: Path,
    minimum_seconds: Annotated[float, typer.Option(min=0.01)] = 60.0,
    margin: Annotated[float, typer.Option(min=1.0)] = 1.15,
):
    """Estimate N from the fastest measured input-processing pass, with a margin."""
    previous = read_workload_stdout(timings)
    fastest = min(float(item["input_processing_wall_seconds"]) for item in previous["passes"])
    if fastest <= 0:
        raise ValueError("A prior actual processing time is required")
    print(math.ceil(minimum_seconds / fastest * margin))


def validate_histogram(candidate, accepted):
    import numpy as np

    if candidate.files != accepted.files or len(candidate["states"]) != 97:
        raise ValueError("Histogram comparison requires the same ordered fields and all 97 states")
    for key in accepted.files:
        new, original = candidate[key], accepted[key]
        if new.dtype != original.dtype or new.shape != original.shape or new.tobytes() != original.tobytes():
            raise ValueError(f"Saved histogram field {key} differs from the accepted same-variant result")
    digest = sha256()
    for key in ("count", "sumw", "sumw2"):
        digest.update(np.ascontiguousarray(accepted[key]).tobytes())
    return digest.hexdigest()


def same_sum(actual, rows, key):
    return math.isclose(float(actual), sum(float(row[key]) for row in rows), rel_tol=1e-12, abs_tol=1e-12)


def placement(directory, entrypoint, collector_cpu, sampled, allow_transient_fork=False):
    observed = defaultdict(list)
    collectors = defaultdict(list)
    perf_path = directory.parent / "probe" / "perf.path"
    collector_name = Path(perf_path.read_text().strip()).resolve().name if perf_path.exists() else "perf"
    for line in (directory / "processes.jsonl").read_text().splitlines():
        snapshot = json.loads(line)
        for process in snapshot["processes"]:
            process["wall_time_unix_ns"] = snapshot["wall_time_unix_ns"]
            executable = Path(process["executable"]).name
            if executable.startswith("python") and any(
                (Path(process["cwd"]) / token).resolve() == entrypoint
                for token in process["argv"] if Path(token).name == entrypoint.name
            ):
                observed[process["pid"]].append(process)
            if executable == collector_name:
                collectors[process["pid"]].append(process)
    transient_forks = []
    if allow_transient_fork and len(observed) > 1:
        owners = set(observed)
        for child_pid, rows in list(observed.items()):
            row = rows[0]
            if (len(rows) == 1 and row["ppid"] in owners
                    and row["affinity"] == str(ANALYSIS_CPU) and row["threads"] == 1):
                transient_forks.append({
                    "pid": child_pid, "parent": row["ppid"], "same_image_samples": len(rows),
                    "affinity": row["affinity"], "threads": row["threads"],
                })
                del observed[child_pid]
    if len(observed) != 1:
        raise ValueError(f"{directory} must observe one actual CPython owner for {entrypoint}. Found {list(observed)}")
    (pid, records), = observed.items()
    cpus = sorted({row["affinity"] for row in records})
    threads = sorted({row["threads"] for row in records})
    if cpus != [str(ANALYSIS_CPU)] or threads != [1]:
        raise ValueError(f"{directory} analysis process has affinities {cpus} and thread counts {threads}")
    perf_forks = []
    persistent = set(collectors)
    for child, rows in list(collectors.items()):
        if len(rows) == 1 and rows[0]["ppid"] in persistent and rows[0]["affinity"] == str(collector_cpu):
            perf_forks.append({"pid": child, "parent": rows[0]["ppid"], "same_image_samples": 1,
                               "affinity": rows[0]["affinity"]})
            del collectors[child]
    collector_affinity = {
        key: sorted({row["affinity"] for row in values}) for key, values in collectors.items()
    }
    collector_startup = []
    if sampled:
        first_owner_observation = records[0]["wall_time_unix_ns"]
        if len(collectors) != 1:
            raise ValueError(f"{directory} does not have one observed persistent perf collector")
        (collector_pid, collector_records), = collectors.items()
        pinned_with_owner = False
        for row in collector_records:
            before_owner = row["wall_time_unix_ns"] < first_owner_observation
            if row["affinity"] == str(collector_cpu):
                pinned_with_owner |= not before_owner
            elif before_owner:
                collector_startup.append({"pid": collector_pid, "affinity": row["affinity"],
                                          "threads": row["threads"], "wall_time_unix_ns": row["wall_time_unix_ns"]})
            else:
                raise ValueError(f"{directory} perf collector left CPU {collector_cpu} during analysis")
        if not pinned_with_owner:
            raise ValueError(f"{directory} does not observe the perf collector pinned during analysis")
    return {
        "pid": pid, "rows_observed": len(records), "affinity": cpus,
        "threads": threads, "binary": file_identity(records[0]["executable"]),
        "argv": records[0]["argv"], "perf_collector_affinity": collector_affinity,
        "transient_same_image_forks": transient_forks,
        "transient_same_image_perf_forks": perf_forks,
        "perf_collector_startup_other_affinity": collector_startup,
    }


def runtime_identity(directory):
    path = directory / "runtime.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    if payload["threads"] != 1 or payload["cpu"] != [ANALYSIS_CPU]:
        raise ValueError(f"{path} does not report the required final placement")
    provider = payload["provider"]
    core = payload["core_module"]
    if not all(any(str(Path(item).resolve()) in mapping for mapping in payload["maps"]) for item in (provider, core)):
        raise ValueError(f"{path} does not show the declared provider and core loaded in the process")
    return {
        **{name: file_identity(payload[name]) for name in ("provider", "payload", "core_module", "executable")},
        "python": payload["python"], "plugin_api": payload["plugin_api"], "plugin_build_id": payload["plugin_build_id"],
        "record": file_identity(path),
    }


def run_evidence(directory, accepted, entrypoint, repeat, collector_cpu, sampled):
    import numpy as np

    histogram = directory / "histogram.npz"
    summary = read_workload_stdout(directory / "workload.stdout")
    if Path(summary["output"]).resolve() != histogram.resolve():
        raise ValueError("The workload summary identifies a different output histogram")
    with np.load(histogram, allow_pickle=False) as candidate:
        digest = validate_histogram(candidate, accepted)
    passes = summary["passes"]
    if [item["pass"] for item in passes] != list(range(1, repeat + 1)):
        raise ValueError("The requested N must match the successful pass list")
    for row in passes:
        if (row["events"], row["muons"], row["histogram_sha256"]) != (20_000, 14_997, digest):
            raise ValueError(f"Pass {row['pass']} differs from the full accepted original-file workflow")
        for field in ("input_processing_wall_seconds", "input_processing_cpu_seconds"):
            if not math.isfinite(row[field]) or row[field] <= 0:
                raise ValueError(f"Pass {row['pass']} has invalid {field}")
    if (summary["events"], summary["muons"], summary["event_visits"], summary["muon_visits"]) != (
        20_000, 14_997, repeat * 20_000, repeat * 14_997
    ):
        raise ValueError("Pass summary does not distinguish the unique inputs from total visits")
    if not all(same_sum(summary[name], passes, name)
               for name in ("input_processing_wall_seconds", "input_processing_cpu_seconds")):
        raise ValueError("Run and per-pass processing time totals disagree")
    match = TIME.search((directory / "time.txt").read_text())
    if not match or int(match[4]):
        raise ValueError(f"Missing successful command timing in {directory}")
    wall, user, system = map(float, match.groups()[:3])
    return {
        "measured_wall_seconds": wall, "measured_user_cpu_seconds": user, "measured_system_cpu_seconds": system,
        "input_processing_wall_seconds": summary["input_processing_wall_seconds"],
        "input_processing_cpu_seconds": summary["input_processing_cpu_seconds"],
        "outside_pass_wall_seconds": wall - summary["input_processing_wall_seconds"],
        "unique_events": summary["events"], "unique_muons": summary["muons"],
        "event_visits": summary["event_visits"], "muon_visits": summary["muon_visits"],
        "passes": passes, "accepted_histogram_array_equal": True, "histogram": file_identity(histogram),
        "placement": placement(directory, entrypoint, collector_cpu, sampled),
        "native_runtime": runtime_identity(directory),
    }


@dataclass(frozen=True)
class Frame:
    dso: str
    symbol: str

    @property
    def resolved(self):
        return self.symbol not in UNKNOWN and not re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", self.symbol)


def frame_from(line):
    match = FRAME.match(line)
    if not match:
        return None
    name, library = match.groups()
    return Frame(library, OFFSET.sub("", name.strip()) or "[unknown]")


def sample_rows(path, losses, unparsed_losses):
    owner = None
    stack = []
    with path.open(errors="replace") as stream:
        for line in stream:
            if "PERF_RECORD_LOST" in line:
                if match := LOSS.search(line):
                    losses[match[1]] += int(match[2])
                else:
                    unparsed_losses.append(line.strip())
            elif match := SAMPLE.match(line):
                if owner is not None:
                    yield owner, tuple(stack)
                owner = (match["comm"], int(match["pid"]), int(match["tid"] or match["pid"]), int(match["cpu"]))
                leaf = frame_from(match["leaf"])
                stack = [leaf] if leaf else []
            elif owner is not None and (frame := frame_from(line)) is not None:
                stack.append(frame)
    if owner is not None:
        yield owner, tuple(stack)


def category(frame):
    if not frame.resolved:
        return "Unresolved symbols"
    symbol, dso = frame.symbol.lower(), frame.dso.lower()
    lib = Path(dso).name
    if "libatlas_muon" in lib:
        return "ATLAS provider"
    if "libcorrectionlib" in lib or "/correctionlib/" in dso:
        return "CorrectionLib evaluator and bindings"
    if re.search(r"(^|[/_])(boost_histogram|boosthistogram)([/_.]|$)", dso):
        return "Histogram storage"
    if any(item in dso for item in ("awkward", "uproot")):
        return "Awkward and uproot native libraries"
    if any(item in lib for item in ("libz.", "libzstd", "liblz4", "liblzma", "libbz2", "cramjam", "libdeflate")):
        return "Compression libraries"
    if any(item in symbol for item in ("malloc", "realloc", "memcpy", "memmove", "operator new", "memset")):
        return "Memory routines, stage unassigned"
    if "numpy" in dso:
        return "NumPy, stage unassigned"
    if "python" in lib:
        return "CPython, stage unassigned"
    return "Other resolved symbols, stage unassigned"


def write_csv(path, rows, columns):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def sampled_profile(directory, owner_pid, output, top):
    exclusive, inclusive, stacks, owners = Counter(), Counter(), Counter(), Counter()
    depths = Counter()
    leaves_only = 0
    analysis_threads = set()
    analysis_cpus = set()
    losses, unparsed_losses = Counter(), []
    for owner, stack in sample_rows(directory / "perf.script", losses, unparsed_losses):
        comm, pid, tid, cpu = owner
        owners[(comm, pid, tid, cpu)] += 1
        if pid == owner_pid:
            analysis_threads.add(tid)
            analysis_cpus.add(cpu)
        depths[len(stack)] += 1
        if len(stack) < 2:
            leaves_only += 1
        stack = stack or (Frame("[unknown]", "[unknown]"),)
        exclusive[stack[0]] += 1
        inclusive.update(set(stack))
        stacks[stack] += 1
    count = exclusive.total()
    analysis_count = sum(n for (_comm, pid, _tid, _cpu), n in owners.items() if pid == owner_pid)
    if not count or not analysis_count or analysis_threads != {owner_pid} or analysis_cpus != {ANALYSIS_CPU}:
        raise ValueError("Recorded samples must include the observed one-thread Python process on CPU 18")
    capture = re.findall(r"\((\d+) samples?\)", (directory / "workload.stderr").read_text(errors="replace"))
    reported = int(capture[-1]) if capture else None
    if reported is not None and reported != count:
        raise ValueError(f"Parsed {count} sampled events but perf record reports {reported}")

    def share(n):
        return 100.0 * n / count

    def symbol_row(frame, n):
        return {"dso": frame.dso, "symbol": frame.symbol, "category": category(frame), "resolved": frame.resolved,
                "samples": n, "sample_share_percent": share(n)}

    columns = ("dso", "symbol", "category", "resolved", "samples", "sample_share_percent")
    exclusive_rows = [symbol_row(frame, n) for frame, n in exclusive.most_common()]
    write_csv(output / "exclusive-symbols.csv", exclusive_rows, columns)
    write_csv(output / "inclusive-symbols.csv",
              (symbol_row(frame, n) for frame, n in inclusive.most_common()), columns)
    write_csv(output / "inclusive-stacks.csv", (
        {"samples": n, "sample_share_percent": share(n),
         "leaf_to_root": " → ".join(f"{frame.symbol} ({frame.dso})" for frame in stack)}
        for stack, n in stacks.most_common()
    ), ("samples", "sample_share_percent", "leaf_to_root"))
    write_csv(output / "sample-ownership.csv", (
        {"comm": comm, "pid": pid, "tid": tid, "cpu": cpu, "samples": n}
        for (comm, pid, tid, cpu), n in owners.most_common()
    ), ("comm", "pid", "tid", "cpu", "samples"))

    def groups(rows):
        members = defaultdict(list)
        for row in rows:
            members[row["category"], row["dso"]].append(row)
        groups = []
        for (name, dso), symbols in members.items():
            n = sum(row["samples"] for row in symbols)
            groups.append({"category": name, "dso": dso, "samples": n, "sample_share_percent": share(n),
                           "examples": [row["symbol"] for row in symbols[:3]]})
        return sorted(groups, key=lambda row: (-row["samples"], row["category"], row["dso"]))

    all_groups = groups(exclusive_rows)
    remainder = groups(exclusive_rows[top:])
    json_new(output / "exclusive-group-remainder.json", {
        "total_samples": count, "all_groups": all_groups, "listed_symbols": exclusive_rows[:top],
        "remainder_samples": sum(row["samples"] for row in remainder), "remainder_groups": remainder,
    })
    n_unresolved = sum(n for frame, n in exclusive.items() if not frame.resolved)
    return {
        "event": EVENT, "requested_hz": 99, "call_graph": "dwarf",
        "scope": "All captured user-mode samples of the launched process tree, including startup and final output",
        "denominator": "Captured sample count, with each recorded observation counted once",
        "samples": count, "perf_reported_samples": reported, "python_owner_samples": analysis_count,
        "python_owner_sample_share_percent": share(analysis_count), "python_owner_cpu": sorted(analysis_cpus),
        "python_owner_tids": sorted(analysis_threads),
        "lost_samples": None if unparsed_losses else sum(losses.values()),
        "lost_by_record_type": dict(losses), "unparsed_loss_records": unparsed_losses,
        "unresolved_leaf_samples": n_unresolved, "unresolved_leaf_share_percent": share(n_unresolved),
        "stacks_with_multiple_frames": count - leaves_only, "stack_depth_counts": dict(depths),
        "inclusive_rows_overlap": True, "sample_phase_assignment_performed": False,
        "artifacts": {name: file_identity(directory / name) for name in
                      ("perf.data", "perf.script", "perf-self-report.txt", "perf-call-report.txt",
                       "perf-event-stats.txt", "perf-build-ids.txt")},
    }


@app.command()
def analyze(
    directory: Path, expected_histogram: Path, entrypoint: Path,
    repeat: Annotated[int, typer.Argument(min=1)],
    minimum_seconds: Annotated[float, typer.Option(min=0)] = 60.0,
    collector_cpu: Annotated[int, typer.Option(min=0)] = 18,
    top_symbols: Annotated[int, typer.Option(min=1)] = 12,
    targeted_only: bool = False,
):
    """Verify repeat work, matching outputs, placement, samples and overhead."""
    import numpy as np

    result = directory / "summary"
    result.mkdir(exist_ok=True)
    with np.load(expected_histogram, allow_pickle=False) as accepted:
        control = run_evidence(directory / "control", accepted, entrypoint.resolve(), repeat, collector_cpu, False)
        sampled = None if targeted_only else run_evidence(
            directory / "sampled", accepted, entrypoint.resolve(), repeat, collector_cpu, True
        )
    source = directory / "probe" / "counter-support.json"
    evidence = {
        "python_script": file_identity(entrypoint), "accepted_histogram": file_identity(expected_histogram),
        "analysis_cpu": ANALYSIS_CPU, "collector_cpu": collector_cpu, "repeat": repeat,
        "minimum_processing_seconds": minimum_seconds,
        "control": control, "sampled": sampled,
        "counter_support": json.loads(source.read_text()) if source.exists() else None,
        "perf_version": (directory / "probe" / "perf.version").read_text().strip()
                        if (directory / "probe" / "perf.version").exists() else None,
        "timing_scope": "The timer identified in configuration.txt spans command launch through exit. Each pass spans original-file open through final fill and source close.",
    }
    if targeted_only:
        denial = directory / "probe" / "sampling-denied.txt"
        denied = denial.exists() and bool(denial.read_text().strip())
        if (evidence["counter_support"] and evidence["counter_support"]["software"]["events"][EVENT]["supported"]
                and not denied):
            raise ValueError("Use the native-sampling route when the software event is available")
        evidence["profile_unavailable"] = {
            "reason": ("Kernel or permissions denied software sampling on the real workload" if denied else
                       "The software CPU-clock probe did not return a usable value"),
            "probe": str((directory / "probe").resolve()),
        }
    else:
        for field in ("provider", "payload", "core_module", "executable"):
            a, b = control["native_runtime"], sampled["native_runtime"]
            if (a is None) != (b is None) or (a and a[field]["sha256"] != b[field]["sha256"]):
                raise ValueError("The sampled and uninstrumented commands used different runtime artifacts")
        if control["placement"]["binary"]["sha256"] != sampled["placement"]["binary"]["sha256"]:
            raise ValueError("The sampled and uninstrumented commands used different Python executables")
        fields = ("measured_wall_seconds", "input_processing_wall_seconds", "input_processing_cpu_seconds")
        evidence["sampled_to_control_ratios"] = {name: sampled[name] / control[name] for name in fields}
        evidence["profile"] = sampled_profile(directory / "sampled", sampled["placement"]["pid"], result, top_symbols)
        if (sampled["placement"]["perf_collector_startup_other_affinity"]
                and evidence["profile"]["python_owner_samples"] != evidence["profile"]["samples"]):
            raise ValueError("Unexpected sample owners with an off-CPU collector startup observation")
        evidence["minimum_met"] = sampled["input_processing_wall_seconds"] >= minimum_seconds
    json_new(result / "evidence.json", evidence)
    if sampled and not evidence["minimum_met"]:
        raise typer.BadParameter("The actual sampled pass processing time fell below the requested minimum")


if __name__ == "__main__":
    app()
