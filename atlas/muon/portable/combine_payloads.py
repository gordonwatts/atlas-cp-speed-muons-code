"""Create one schema-v3 payload for a new candidate checkpoint."""

import json
from pathlib import Path
from typing import Annotated

import typer


def main(
    destination: Annotated[Path, typer.Argument(dir_okay=False)],
    components: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False)],
):
    definitions = {}
    compounds = {}
    for path in components:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != 3:
            raise ValueError(f"Expected schema v3: {path}")
        for kind, collection in (("corrections", definitions), ("compound_corrections", compounds)):
            for item in payload.get(kind, []):
                if item["name"] in collection:
                    raise ValueError(f"Duplicate correction at {path}: {item['name']}")
                collection[item["name"]] = item
    result = {"schema_version": 3, "corrections": list(definitions.values())}
    if compounds:
        result["compound_corrections"] = list(compounds.values())
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, separators=(",", ":"), allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    typer.run(main)
