"""Check existing timing process evidence and report active campaign processes."""

import json
import os
from pathlib import Path

import typer


def scope_receipt(directory, cohort, bridge):
    rows = []
    for receipt in sorted(directory.glob("*/*.log.json")):
        record = json.loads(receipt.read_text())
        samples = record["samples"]
        assert record["exit_code"] == 0 and samples
        assert all(sample == {"threads": 1, "affinity": "18"} for sample in samples), receipt
        libraries = set(record["observed_libraries"])
        agent = receipt.parent.name[-1]
        mode = receipt.parent.name.split("-")[-2]
        mapped_bridge = str(bridge) in libraries
        mapped_provider = any(Path(path).name == "libatlas_muon.so" for path in libraries)
        mapped_core = any(Path(path).name == "libcorrectionlib.so" for path in libraries)
        if cohort != "combined_columnar":
            if receipt.name == "framework.log.json" and agent == "B":
                assert mapped_bridge and mapped_provider == (mode != "stored"), receipt
            if receipt.name == "framework.log.json" and agent == "A":
                assert not mapped_bridge and not mapped_provider, receipt
            if receipt.name == "columnar.log.json":
                assert not mapped_bridge and mapped_provider == mapped_core == (mode != "stored"), receipt
        else:
            assert mapped_provider and mapped_core and not mapped_bridge, receipt
        rows.append({"receipt": str(receipt), "samples": len(samples), "analysis_cpu": 18, "threads": 1,
                     "candidate_bridge_mapped": mapped_bridge, "provider_mapped": mapped_provider,
                     "core_mapped": mapped_core})
    return rows


def related_processes(campaign):
    ancestors = {os.getpid()}
    parent = os.getppid()
    while parent > 1 and parent not in ancestors:
        ancestors.add(parent)
        try:
            status = (Path("/proc") / str(parent) / "status").read_text()
            parent = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
        except (FileNotFoundError, ProcessLookupError):
            break
    records = []
    for proc in Path("/proc").glob("[0-9]*"):
        if int(proc.name) in ancestors:
            continue
        try:
            command = (proc / "cmdline").read_bytes().split(b"\x00")
            cwd = (proc / "cwd").readlink()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        root = os.fsencode(campaign)
        if cwd.is_relative_to(campaign) or any(root in argument for argument in command):
            records.append({"pid": int(proc.name), "program": os.fsdecode(command[0]), "cwd": str(cwd)})
    return records


def main(campaign: Path, output: Path):
    results = campaign / "results"
    cp15 = results / "checkpoint-15"
    bridge = cp15 / "b01-validation-02/candidate-build/libAtlasMuonFramework.so"
    sources = {
        "full_final_bracket": cp15 / "final-b01-bracket-01",
        "nominal_framework_recheck": cp15 / "final-b01-nominal-recheck-01",
        "combined_columnar": results / "checkpoint-17/faithful-vs-installed-01/paired",
    }
    cohorts = {name: scope_receipt(path, name, bridge) for name, path in sources.items()}
    assert tuple(map(len, cohorts.values())) == (90, 24, 12)
    processes = related_processes(campaign)
    report = {"launches_verified": sum(map(len, cohorts.values())), "cohorts": cohorts,
              "campaign_processes_after_all_jobs": processes, "quiescent": not processes}
    with output.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    if processes:
        raise RuntimeError(f"Campaign processes still active: {processes}")


if __name__ == "__main__":
    typer.run(main)
