"""Sample guest CPU use outside production timers."""

import os
from pathlib import Path
import time


def processes():
    result = {}
    for item in Path("/proc").iterdir():
        if not item.name.isdecimal():
            continue
        try:
            stat = (item / "stat").read_text(errors="replace")
            comm, tail = stat.split("(", 1)[1].rsplit(")", 1)
            fields = tail.split()
            if fields[0] in {"X", "Z"}:
                continue
            result[(int(item.name), int(fields[19]))] = {"ticks": int(fields[11]) + int(fields[12]), "last_cpu": int(fields[36]), "name": comm}
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


def host_activity():
    before = processes()
    time.sleep(0.25)
    after = processes()
    active = []
    for (pid, start), current in after.items():
        previous = before.get((pid, start), current)
        ticks = current["ticks"] - previous["ticks"]
        if ticks <= 0:
            continue
        root = Path(f"/proc/{pid}")
        try:
            status = dict(line.split(":", 1) for line in (root / "status").read_text(errors="replace").splitlines())
            cwd = os.readlink(root / "cwd").lower()
            command = (root / "cmdline").read_bytes().lower()
            workload = next((tag for tag in ("calratio", "large-r", "small-r", "jetcalib", "muon-skill-c8a2374") if tag in cwd or tag.encode() in command), "unclassified")
            active.append({key: value for key, value in current.items() if key != "ticks"} | {"pid": pid, "cpu_ticks_in_sample": ticks, "cpu_affinity": status["Cpus_allowed_list"].strip(), "threads": int(status["Threads"]), "workload": workload})
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return {"sample_seconds": 0.25, "load": os.getloadavg(), "active": sorted(active, key=lambda item: -item["cpu_ticks_in_sample"])[:20]}
