#!/usr/bin/env python3
"""Attribute a full pass of the installed, unchanged visible script."""

from __future__ import annotations

import sys
import time


def capture(demo, original, destination, evidence):
    with open(demo, encoding="utf-8") as handle:
        lines = handle.read().splitlines(keepends=True)
    source = "".join(lines)

    def line_at(prefix):
        matching = [index for index, line in enumerate(lines, 1) if line.strip().startswith(prefix)]
        assert len(matching) == 1, (prefix, matching)
        return matching[0]

    setup_end = line_at("pass_summaries =")
    scope = {
        "read_and_decode": (line_at("with uproot.open("), line_at("if set(arrays) !=")),
        "prepare_and_preselection": (line_at("chunk = MuonChunk("), line_at("kinematics = {}")),
        "selection": (line_at("chosen = working_point.evalv("), line_at("kinematics[kin_state] =")),
    }
    exact = {
        line_at("plugins.load_library("): "provider_load",
        line_at("correction_set = _core.CorrectionSet.from_file("): "payload_load",
        line_at("m = momentum.evalv("): "momentum",
        line_at("weights = combined_efficiency.evalv("): "product",
        line_at("weighted.fill(pt_gev, state,"): "histogram_fill_weighted",
    }
    unweighted_fill = line_at("unweighted.fill(pt_gev, state,")
    efficiency_call = line_at("sf = node.evalv(")

    def label(code, number):
        if lines[number - 1].strip().startswith(("import ", "from ")):
            return "imports"
        if number in exact:
            return exact[number]
        if code.co_name == "fill_pass":
            for name, (start, stop) in scope.items():
                if start <= number < stop:
                    return name
            return "pass_control_and_array_operations"
        if code.co_name == "main":
            return "analysis_initialization" if number < setup_end else "histogram_digest_write_and_summary"
        return "module_and_cli_setup"

    executable = compile(source, demo, "exec")
    code_type = type(executable)

    def code_objects(parent):
        yield parent
        for value in parent.co_consts:
            if isinstance(value, code_type):
                yield from code_objects(value)

    selected = [code for code in code_objects(executable) if code.co_name in {"<module>", "main", "fill_pass"}]
    assert {code.co_name for code in selected} == {"<module>", "main", "fill_pass"}
    wall = {}
    cpu = {}
    calls = {}
    prior = "trace_start"
    last_wall = time.perf_counter_ns()
    last_cpu = time.process_time_ns()

    def milestone(name):
        nonlocal prior, last_wall, last_cpu
        new_wall = time.perf_counter_ns()
        new_cpu = time.process_time_ns()
        wall[prior] = wall.get(prior, 0) + new_wall - last_wall
        cpu[prior] = cpu.get(prior, 0) + new_cpu - last_cpu
        calls[name] = calls.get(name, 0) + 1
        prior, last_wall, last_cpu = name, new_wall, new_cpu

    def line_event(code, number):
        if number == efficiency_call:
            tool_label = sys._getframe(1).f_locals["label"]
            milestone(f"efficiency_{tool_label}")
            return
        if number == unweighted_fill:
            values = sys._getframe(1).f_locals
            family = "weight_only" if values["state"] != values["kin_state"] else "kinematic"
            milestone(f"histogram_fill_unweighted_{family}")
            return
        milestone(label(code, number))

    trace = sys.monitoring
    tool = 3
    trace.use_tool_id(tool, "installed-muon-phases")
    trace.register_callback(tool, trace.events.LINE, line_event)
    for code in selected:
        trace.set_local_events(tool, code, trace.events.LINE)
    sys.argv = [demo, original, destination]
    sys.path[0] = demo.rsplit("/", 1)[0]
    module = type(sys)("__main__")
    module.__file__ = demo
    sys.modules["__main__"] = module
    try:
        exec(executable, module.__dict__)
    except SystemExit as status:
        if status.code not in (None, 0):
            raise
    finally:
        milestone("trace_finish")
        for code in selected:
            trace.set_local_events(tool, code, 0)
        trace.free_tool_id(tool)
    import json
    assert calls["histogram_fill_unweighted_weight_only"] == 680
    assert calls["histogram_fill_unweighted_kinematic"] == 290
    assert calls["histogram_fill_weighted"] == 970
    assert all(calls[f"efficiency_{name}"] > 0 for name in ("RECO", "ISO", "TTVA"))
    with open(evidence, "x") as output:
        json.dump({
            "wall_seconds_by_phase": {name: value / 1e9 for name, value in wall.items()},
            "cpu_seconds_by_phase": {name: value / 1e9 for name, value in cpu.items()},
            "line_events": calls, "anchor_lines": {name: number for number, name in exact.items()},
            "unweighted_fill_anchor_line": unweighted_fill,
            "efficiency_call_anchor_line": efficiency_call,
            "phase_source_ranges_half_open": scope,
        }, output, indent=2)
        output.write("\n")


def main(destination: Path, demo: Path, original: Path, expected: Path):
    import hashlib
    import json
    import os

    from benchmark_reference import time_process

    destination.mkdir()
    report = {"visible_script": str(demo), "visible_script_sha256": hashlib.sha256(demo.read_bytes()).hexdigest(),
              "interpreter": sys.executable, "scope": "One full physical pass per condition on CPU 18"}
    environment = dict(os.environ)
    trace_file = destination / "instrumented-phases.json"
    for kind in ("control", "instrumented"):
        directory = destination / kind
        directory.mkdir()
        histogram = directory / "histogram.npz"
        command = [sys.executable, str(demo), str(original), str(histogram)]
        if kind == "instrumented":
            command = [sys.executable, __file__, "--instrument-visible", str(demo), str(original), str(histogram), str(trace_file)]
        timing = time_process(command, directory, "job.log", environment)
        assert histogram.read_bytes() == expected.read_bytes()
        summary = next(json.loads(line) for line in (directory / "job.log").read_text().splitlines() if line.startswith("{"))
        assert (summary["events"], summary["muons"], len(summary["states"])) == (20_000, 14_997, 97)
        report[kind] = {name: timing[name] for name in ("wall_seconds", "cpu_total_seconds")}
        report[kind]["input_processing_wall_seconds"] = summary["input_processing_wall_seconds"]
        report[kind]["input_processing_cpu_seconds"] = summary["input_processing_cpu_seconds"]
        report[kind]["outside_input_processing_wall_seconds"] = timing["wall_seconds"] - summary["input_processing_wall_seconds"]
        report[kind]["endpoint_sha256"] = hashlib.sha256(histogram.read_bytes()).hexdigest()
    phases = json.loads(trace_file.read_text())
    report["instrumented"]["wall_outside_visible_code_seconds"] = report["instrumented"]["wall_seconds"] - sum(phases["wall_seconds_by_phase"].values())
    report["instrumented"]["cpu_outside_visible_code_seconds"] = report["instrumented"]["cpu_total_seconds"] - sum(phases["cpu_seconds_by_phase"].values())
    report["instrumented_to_uninstrumented_wall_ratio"] = report["instrumented"]["wall_seconds"] / report["control"]["wall_seconds"]
    report["phases"] = phases
    with (destination / "phase-comparison.json").open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--instrument-visible"]:
        capture(*sys.argv[2:])
    else:
        from pathlib import Path
        import typer

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/muon_reference"))
        typer.run(main)
