"""Make report tables from complete matched A/B/C benchmark receipts."""

import json
import math
from pathlib import Path
import statistics

MODES = {"stored": "Read without corrections", "nominal": "Nominal", "full": "Full, 97 states"}
PARTS = ("A_framework", "A_histogram", "A_total", "B_framework", "B_histogram", "B_total", "C_total")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_mode(summary, rows):
    measured = [row["paths"] for row in rows if not row["warmup"]]
    if len(rows) != 6 or len(measured) != 5:
        raise ValueError("Expected one warm-up and five complete matched measurements")
    for row in rows:
        if set(row["paths"]) != set("ABC"):
            raise ValueError("Each block must contain all three workflows")
        if len({row["paths"][path]["histogram_sha256"] for path in "ABC"}) != 1:
            raise ValueError("Saved outputs differ within a matched block")
    for part in PARTS:
        path, leg = part.split("_")
        for metric in ("wall_seconds", "cpu_total_seconds"):
            records = [block[path]["total"] if leg == "total" else block[path]["legs"][leg] for block in measured]
            values = [record[metric] for record in records]
            cohort = summary["cohorts"][part][metric]
            for actual, key in ((statistics.mean(values), "mean_seconds"),
                                (statistics.stdev(values), "sample_sd_seconds"),
                                (statistics.median(values), "median_seconds")):
                if not math.isclose(actual, cohort[key], rel_tol=1e-12):
                    raise ValueError(f"{part} {key} does not agree with the raw ledger")


def cohort_table(summaries, metric):
    rows = ["| Workload | A framework | A histogram | A total | B framework | B histogram | B total | C direct |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for mode, name in MODES.items():
        cohorts = summaries[mode]["cohorts"]
        cells = []
        for part in PARTS:
            record = cohorts[part][metric]
            flag = "" if record["timing_quality_pass"] else " †"
            cells.append(f"{record['mean_seconds']:.3f} ± {record['sample_sd_seconds']:.3f}{flag}")
        rows.append("| " + " | ".join([name, *cells]) + " |")
    return rows


def ratios(summaries):
    rows = ["| Workload | A total / C direct | B framework / A framework |",
            "| --- | ---: | ---: |"]
    for mode, name in MODES.items():
        record = summaries[mode]
        cells = []
        for key, parts in (("A_total_over_C_total", ("A_total", "C_total")),
                           ("B_framework_over_A_framework", ("A_framework", "B_framework"))):
            ratio = record["matched"]["wall_seconds"][key]
            low, high = ratio["interval95"]
            good = all(record["cohorts"][part]["wall_seconds"]["timing_quality_pass"] for part in parts)
            symbol = "" if good else " †"
            cells.append(f"{ratio['geometric_mean']:.3f} ({low:.3f}–{high:.3f}){symbol}")
        rows.append("| " + " | ".join([name, *cells]) + " |")
    return rows


def main(stored: Path, nominal: Path, full: Path):
    """Select the series for each mode explicitly, retaining bounded repeats."""
    summaries = {}
    for mode, folder in (("stored", stored), ("nominal", nominal), ("full", full)):
        summary = read(folder / "benchmark.json")["summaries"][mode]
        validate_mode(summary, [row for row in read(folder / "runs.json") if row["mode"] == mode])
        summaries[mode] = summary
    for heading, lines in (("Wall time, mean ± sample SD (s)", cohort_table(summaries, "wall_seconds")),
                           ("Process CPU time, mean ± sample SD (s)", cohort_table(summaries, "cpu_total_seconds")),
                           ("Paired geometric mean wall-time ratios (95% interval)", ratios(summaries))):
        print("\n".join([heading, "", *lines, ""]))


if __name__ == "__main__":
    import typer

    typer.run(main)
