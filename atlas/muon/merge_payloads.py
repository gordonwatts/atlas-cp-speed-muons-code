#!/usr/bin/env python3
"""Build one deterministic schema-v3 payload without altering its leased stages."""

import hashlib
import json
from pathlib import Path

import typer


def main(destination: Path, components: list[Path]):
    bundle = {"schema_version": 3, "description": "ATLAS Run 3 muon calibration, selection and efficiency", "corrections": []}
    names = set()
    sources = []
    for component in components:
        payload = json.loads(component.read_bytes())
        if payload["schema_version"] != 3 or set(payload) - {"schema_version", "description", "corrections"}:
            raise ValueError(f"Unexpected schema or compound nodes in {component}")
        sources.append({"file": str(component), "sha256": hashlib.file_digest(component.open("rb"), "sha256").hexdigest()})
        for correction in payload["corrections"]:
            name = correction["name"]
            if name in names:
                raise ValueError(f"Duplicate correction {name}")
            names.add(name)
            bundle["corrections"].append(correction)
    with destination.open("x") as stream:
        json.dump(bundle, stream, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
    print(json.dumps({"destination": str(destination), "file_bytes": destination.stat().st_size, "corrections": sorted(names), "sources": sources}, indent=2))


if __name__ == "__main__":
    typer.run(main)
