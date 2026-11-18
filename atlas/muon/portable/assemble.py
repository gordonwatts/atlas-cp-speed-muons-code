"""Assemble a demo and two local wheels from explicit native release artifacts."""

from base64 import urlsafe_b64encode
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from typing import Annotated
import zipfile

from packaging.tags import Tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version
import typer

HERE = Path(__file__).resolve().parent
CORE_COMMIT = "3dcfa20ec01fb40c4d5fec58383785519dec1c00"
DISTRIBUTION = "atlas-muon-portable"
MODULE = "atlas_muon_portable"
TAG = Tag("cp314", "cp314", "linux_x86_64")
NODES = {
    "muon_selection_eta": 0,
    "muon_selection_vertex": 0,
    "muon_selection_track_roles": 2,
    "muon_selection_after_calibration": 6,
    "muon_eff_reco": 2,
    "muon_eff_iso": 2,
    "muon_eff_ttva": 2,
    "muon_eff_product": 0,
}
EFFICIENCY_INPUTS = ["state", "random_run_number", "pt", "eta", "phi", "charge", "muon_type", "gate"]
OPTIONAL_NODES = {
    "muon_selection_pt": 0, "muon_selection_track": 0, "muon_selection_quality": 2,
    "muon_selection_isolation": 0, "muon_selection_final": 0,
}
ROLE_INPUTS = ["muon_type", "id_link_valid", "me_link_valid", "cb_link_valid", "msonly_link_valid", "ms_link_valid"]
DEPENDENCIES = {"awkward", "hist", "numpy", "typer", "uproot"}
IDENTITY_FIELDS = (
    "core_commit", "plugin_api", "plugin_build_id", "core_wheel_sha256", "provider_sha256",
    "payload_sha256", "workflow_sha256", "raw_reader_sha256", "source_roles_sha256", "profile",
    "build_python", "compiler",
)
ROOT_LIBRARY = re.compile(
    r"^(?:lib(?:ROOT.*|Core|RIO|Hist|Tree.*|MathCore|MathMore|Matrix|Physics|Cling|Imt|Thread|cppyy)|"
    r"lib(?:Asg|Athena|xAOD|MuonMomentumCorrections|MuonSelectionTool).*)\.so(?:\.|$)", re.IGNORECASE,
)
BAD_DISTRIBUTIONS = {"root", "root-numpy", "root-pandas", "pyroot", "cppyy", "cppyy-backend", "cppyy-cling"}
IN = Annotated[Path, typer.Option(exists=True, file_okay=True, dir_okay=False)]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def encoded(obj):
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def checked_pins(path):
    entries = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([-A-Za-z0-9_.]+)==([^\s;]+)", line)
        if not match:
            raise ValueError(f"Expected one exact name==version pin per line: {line}")
        name, value = match.groups()
        key = canonicalize_name(name)
        if key in entries or key in BAD_DISTRIBUTIONS | {"correctionlib", DISTRIBUTION}:
            raise ValueError(f"Unexpected package in the public lock: {name}")
        entries[key] = str(Version(value))
    if not DEPENDENCIES <= entries.keys():
        raise ValueError(f"Missing public dependencies: {DEPENDENCIES - entries.keys()}")
    return entries


def core_wheel_version(wheel):
    name, version, _, tags = parse_wheel_filename(wheel.name)
    if name != "correctionlib" or TAG not in tags:
        raise ValueError(f"Expected a pinned cp314-cp314-linux_x86_64 CorrectionLib wheel: {wheel.name}")
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        if len([name for name in members if name.startswith("correctionlib/_core.") and name.endswith(".so")]) != 1:
            raise ValueError("Expected a single compiled CorrectionLib Python core")
        if "correctionlib/lib/libcorrectionlib.so" not in members:
            raise ValueError("CorrectionLib wheel is missing the provider's linked SDK library")
        if any(ROOT_LIBRARY.search(Path(name).name) for name in members):
            raise ValueError("CorrectionLib wheel contains an unsupported native dependency")
    return str(version)


def provider_preflight(provider):
    commands = [["readelf", "-h", str(provider)], ["readelf", "-d", str(provider)]]
    elf, dynamic = [subprocess.run(command, capture_output=True, check=True, text=True).stdout for command in commands]
    if "ELF64" not in elf or "X86-64" not in elf:
        raise ValueError("Provider must be a Linux x86_64 ELF shared object")
    needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic)
    if "libcorrectionlib.so" not in needed or any("/" in item or ROOT_LIBRARY.search(item) for item in needed):
        raise ValueError(f"Provider contains unsupported dependencies: {needed}")
    for path_list in re.findall(r"\((?:RUNPATH|RPATH)\).*?\[([^\]]+)\]", dynamic):
        if not all(part.startswith(("$ORIGIN/", "${ORIGIN}/")) or part == "$ORIGIN" for part in path_list.split(":")):
            raise ValueError(f"Provider uses an ambient loader path: {path_list}")
    return needed


def verify_payload(path, profile):
    document = json.loads(path.read_text(encoding="utf-8"))
    corrections = document["corrections"]
    expected = {**NODES, "muon_momentum": 12} if profile == "full" else NODES
    if document["schema_version"] != 3:
        raise ValueError("Expected the exact schema-v3 correction set for this profile")
    models = {definition["name"]: definition for definition in corrections}
    if len(models) != len(corrections) or not expected.keys() <= models.keys() <= (expected | OPTIONAL_NODES).keys():
        raise ValueError("Staged payload uses unexpected correction names")
    result = {}
    for name, width in (expected | {name: OPTIONAL_NODES[name] for name in models.keys() - expected.keys()}).items():
        model = models[name]
        shape = model["output"].get("shape")
        if shape != ([width] if width else None):
            raise ValueError(f"Correction {name} must declare output width {width}")
        names = [field["name"] for field in model["inputs"]]
        if name in ("muon_eff_reco", "muon_eff_iso", "muon_eff_ttva") and names != EFFICIENCY_INPUTS:
            raise ValueError(f"Unsupported efficiency ABI in {name}")
        if name == "muon_eff_product" and names != ["reco_sf", "iso_sf", "ttva_sf"]:
            raise ValueError(f"Unsupported combined efficiency ABI in {name}")
        if name == "muon_selection_track_roles" and names != ROLE_INPUTS:
            raise ValueError(f"Unsupported track role ABI in {name}")
        result[name] = {"width": width, "inputs": names}
    return result


def wheel_owned_payload(files, destination, version, requirements):
    dist_info = f"{MODULE}-{version}.dist-info"
    metadata = (
        "Metadata-Version: 2.3\n"
        f"Name: {DISTRIBUTION}\nVersion: {version}\n"
        "Summary: Native provider and original PHYSLITE muon payload\n"
        "Requires-Python: >=3.14,<3.15\n"
        + "".join(f"Requires-Dist: {entry}\n" for entry in requirements)
        + "\n"
    )
    wheel_descriptor = f"Wheel-Version: 1.0\nGenerator: atlas-muon-assembly\nRoot-Is-Purelib: false\nTag: {TAG}\n"
    files[f"{dist_info}/METADATA"] = metadata.encode("utf-8")
    files[f"{dist_info}/WHEEL"] = wheel_descriptor.encode("utf-8")
    record = f"{dist_info}/RECORD"
    csv_text = io.StringIO()
    writer = csv.writer(csv_text, lineterminator="\n")
    for name, data in sorted(files.items()):
        hash_value = urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
        writer.writerow((name, f"sha256={hash_value}", len(data)))
    writer.writerow((record, "", ""))
    files[record] = csv_text.getvalue().encode("utf-8")
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            wheel.writestr(info, data, compresslevel=9)


def render_script(template, destination, core_path, package_path, requirements):
    source = template.read_text(encoding="utf-8")
    rows = [
        "# dependencies = [",
        *(f"#   {json.dumps(item)}," for item in sorted(requirements)),
        "# ]", "#", "# [tool.uv.sources]",
        f'# correctionlib = {{ path = "wheels/{core_path.name}" }}',
        f'# {DISTRIBUTION} = {{ path = "wheels/{package_path.name}" }}',
    ]
    parent_marker = "# @LOCAL_WHEEL_REQUIREMENTS@"
    if source.count(parent_marker) != 1:
        raise ValueError("Expected a single script metadata insertion point")
    source = source.replace(parent_marker, "\n".join(rows), 1)
    front_matter = source.split("# /// script\n", 1)[1].split("# ///\n", 1)[0]
    tomllib.loads("\n".join(line.removeprefix("#").removeprefix(" ") for line in front_matter.splitlines()))
    compile(source, destination.name, "exec")
    destination.write_text(source, encoding="utf-8", newline="\n")


def main(
    core_wheel: IN, provider: IN, payload: IN, workflow: IN, raw_reader: IN, source_roles: IN,
    identity: IN, public_pins: IN,
    output: Annotated[Path, typer.Option(file_okay=False)],
    version: Annotated[str, typer.Option()],
    acceptance: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
):
    """Bundle accepted native bytes without relocating or rebuilding their provider."""
    release = json.loads(identity.read_text(encoding="utf-8"))
    if not set(IDENTITY_FIELDS) <= release.keys():
        raise ValueError(f"Missing artifact receipt fields: {set(IDENTITY_FIELDS) - release.keys()}")
    profile = release["profile"]
    if profile not in ("full", "partial-selection-efficiency"):
        raise ValueError(f"Unexpected delivery profile {profile}")
    if release["core_commit"] != CORE_COMMIT or release["plugin_api"] != 4 or not release["plugin_build_id"]:
        raise ValueError("Artifact receipt must identify the pinned ABI-4 core and build")
    if profile == "full":
        for field in ("original_stream_checkpoint", "delivery_checkpoint"):
            if not re.fullmatch("[0-9a-f]{40}", release[field]):
                raise ValueError(f"Missing delivery coordinate {field}")
        if not release["rng_stream"] or acceptance is None or digest(acceptance) != release["acceptance_sha256"]:
            raise ValueError("Full portable assembly requires numerical delivery evidence and a named RNG stream")
    elif not Version(version).is_devrelease:
        raise ValueError("Partial packaging smoke must use a .devN version")

    paths = {
        "core_wheel_sha256": core_wheel, "provider_sha256": provider, "payload_sha256": payload,
        "workflow_sha256": workflow, "raw_reader_sha256": raw_reader, "source_roles_sha256": source_roles,
    }
    for field, artifact in paths.items():
        if digest(artifact) != release[field]:
            raise ValueError(f"Artifact changed from the input receipt: {artifact}")
    pins = checked_pins(public_pins)
    native_names = provider_preflight(provider)
    core_version = core_wheel_version(core_wheel)
    shapes = verify_payload(payload, profile)
    if not isinstance(json.loads(source_roles.read_text(encoding="utf-8")), dict):
        raise ValueError("Expected an object mapping the portable source roles")
    if len(json.loads(workflow.read_text(encoding="utf-8"))["states"]["all_ordered"]) != 97:
        raise ValueError("Workflow layout does not contain the campaign's 97 states")

    package_version = str(Version(version))
    runtime = {
        **release, "version": package_version, "tag": str(TAG),
        "core": {"version": core_version, "file": core_wheel.name, "sha256": release["core_wheel_sha256"]},
        "corrections": shapes, "public_pins": pins, "provider_needed": native_names,
    }
    files = {
        f"{MODULE}/__init__.py": (HERE / MODULE / "__init__.py").read_bytes(),
        f"{MODULE}/packing.py": (HERE / MODULE / "packing.py").read_bytes(),
        f"{MODULE}/runtime.py": (HERE / MODULE / "runtime.py").read_bytes(),
        f"{MODULE}/raw_input.py": raw_reader.read_bytes(),
        f"{MODULE}/workflow.json": workflow.read_bytes(),
        f"{MODULE}/native/libatlas_muon.so": provider.read_bytes(),
        f"{MODULE}/payload.json": payload.read_bytes(),
        f"{MODULE}/release.json": encoded(runtime),
        f"{MODULE}/source_roles.json": source_roles.read_bytes(),
    }
    requirements = [f"correctionlib=={core_version}", *(f"{key}=={pin}" for key, pin in sorted(pins.items()))]
    wheel_name = f"{MODULE}-{package_version}-{TAG}.whl"
    output.mkdir()
    wheels = output / "wheels"
    wheels.mkdir()
    shutil.copyfile(core_wheel, wheels / core_wheel.name)
    wheel_owned_payload(files, wheels / wheel_name, package_version, requirements)

    script = "muon_demo.py" if profile == "full" else "partial_probe.py"
    render_script(HERE / f"{script}.in", output / script, core_wheel, wheels / wheel_name,
                  [*requirements, f"{DISTRIBUTION}=={package_version}"])
    shutil.copyfile(HERE / "audit_install.py", output / "audit_install.py")
    (output / "public-requirements.txt").write_text(
        "".join(f"{name}=={pin}\n" for name, pin in sorted(pins.items())), encoding="utf-8", newline="\n",
    )
    file_list = sorted(path for path in output.rglob("*") if path.is_file())
    (output / "bundle.json").write_bytes(encoded({
        "profile": profile, "entry_point": script, "tag": str(TAG),
        "files": {
            str(path.relative_to(output)): {"bytes": path.stat().st_size, "sha256": digest(path)}
            for path in file_list
        },
    }))
    print(output.resolve())


if __name__ == "__main__":
    typer.run(main)
