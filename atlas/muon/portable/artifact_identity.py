"""Record the artifacts picked for a source-validated portable delivery."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

import typer

FILE = Annotated[Path, typer.Option(exists=True, dir_okay=False)]
OPTIONAL_FILE = Annotated[Path | None, typer.Option(exists=True, dir_okay=False)]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(
    output: Annotated[Path, typer.Argument(dir_okay=False)],
    profile: Annotated[str, typer.Option()],
    core_commit: Annotated[str, typer.Option()],
    plugin_build_id: Annotated[str, typer.Option()],
    compiler: Annotated[str, typer.Option()],
    build_python: Annotated[str, typer.Option()],
    core_wheel: FILE, provider: FILE, payload: FILE, workflow: FILE, raw_reader: FILE, source_roles: FILE,
    acceptance: OPTIONAL_FILE = None,
    original_stream_checkpoint: Annotated[str | None, typer.Option()] = None,
    delivery_checkpoint: Annotated[str | None, typer.Option()] = None,
    rng_stream: Annotated[str | None, typer.Option()] = None,
):
    receipt = {
        "profile": profile, "core_commit": core_commit, "plugin_api": 4, "plugin_build_id": plugin_build_id,
        "compiler": compiler, "build_python": build_python,
        **{
            f"{role}_sha256": sha256(path)
            for role, path in {
                "core_wheel": core_wheel, "provider": provider, "payload": payload, "workflow": workflow,
                "raw_reader": raw_reader, "source_roles": source_roles,
            }.items()
        },
    }
    if profile == "full":
        if not all((acceptance, original_stream_checkpoint, delivery_checkpoint, rng_stream)):
            raise ValueError("Record the accepted original-stream and deliverable checkpoints, evidence and stream")
        receipt.update(
            acceptance_sha256=sha256(acceptance), original_stream_checkpoint=original_stream_checkpoint,
            delivery_checkpoint=delivery_checkpoint, rng_stream=rng_stream,
        )
    elif profile != "partial-selection-efficiency":
        raise ValueError(f"Unknown packaging profile: {profile}")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    typer.run(main)
