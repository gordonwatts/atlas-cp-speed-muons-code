"""Audit wheel contents, installed native closure, and installation bytes.

The CLI uses the standard library so it can inspect a fresh environment
before installing its application dependencies.
"""

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT_LIBRARY = re.compile(
    r"^(?:lib(?:ROOT.*|Core|RIO|Hist|Tree.*|MathCore|MathMore|Matrix|Physics|Cling|Imt|Thread|cppyy)|"
    r"lib(?:Asg|Athena|xAOD|MuonMomentumCorrections|MuonSelectionTool).*)\.so(?:\.|$)", re.IGNORECASE,
)
BAD_PACKAGE = {"root", "pyroot", "root-numpy", "root-pandas", "cppyy", "cppyy-backend", "cppyy-cling"}
NATIVE = re.compile(r"\.so(?:\.|$)")


def canonical_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def demand(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def unsupported(path):
    leaf = path.name
    return ROOT_LIBRARY.search(leaf) or canonical_name(leaf) in BAD_PACKAGE or any(
        word in str(path).lower() for word in ("/cvmfs/", "/analysisbase/", "/atlasexternals/")
    )


def inspect_archive(path):
    data = {"bytes": path.stat().st_size, "sha256": digest(path)}
    with zipfile.ZipFile(path) as archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        members = [item.filename for item in files]
        demand(len(members) == len(set(members)), f"Duplicate wheel members in {path}")
        demand(not any(name.startswith("/") or ".." in Path(name).parts for name in members), "Unsafe wheel member")
        demand(not any(unsupported(Path(name)) for name in members), f"ROOT or framework files in {path}")
        metadata_file, = [name for name in members if name.endswith(".dist-info/METADATA")]
        wheel_file, = [name for name in members if name.endswith(".dist-info/WHEEL")]
        metadata = email.message_from_bytes(archive.read(metadata_file))
        wheel = email.message_from_bytes(archive.read(wheel_file))
        requires = metadata.get_all("Requires-Dist", [])
        for line in requires:
            name = re.match(r"[a-zA-Z0-9_.-]+", line)
            demand(name and canonical_name(name[0]) not in BAD_PACKAGE, f"Forbidden dependency in {path}: {line}")
        data.update({
            "distribution": metadata["Name"], "version": metadata["Version"],
            "requires_python": metadata["Requires-Python"], "requires_dist": requires,
            "tags": wheel.get_all("Tag", []), "wheel_owned_uncompressed_bytes": sum(item.file_size for item in files),
            "native_members": [name for name in members if NATIVE.search(Path(name).name)],
        })
    return data


def verify_bundle(bundle):
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    actual = {
        str(path.relative_to(bundle)) for path in bundle.rglob("*")
        if path.is_file() and path != bundle / "bundle.json"
    }
    demand(actual == manifest["files"].keys(), "Extracted file list differs from assembly receipt")
    for name, identity in manifest["files"].items():
        path = bundle / name
        demand(path.is_file() and not path.is_symlink(), f"Missing or symlinked bundle member {name}")
        demand(path.stat().st_size == identity["bytes"] and digest(path) == identity["sha256"],
               f"Extracted file differs from assembly receipt: {name}")
    wheels = {path.name: inspect_archive(path) for path in sorted((bundle / "wheels").glob("*.whl"))}
    names = {canonical_name(info["distribution"]) for info in wheels.values()}
    demand(len(wheels) == 2 and names == {"correctionlib", "atlas-muon-portable"}, "Expected the two local wheels")
    demand(all("cp314-cp314-linux_x86_64" in wheel["tags"] for wheel in wheels.values()), "Unexpected wheel tags")
    return manifest, wheels


def scan_environment(root):
    """Count inode storage once. Include directories and symlinks without following them."""
    if root is None or not root.exists():
        return None
    seen = set()
    apparent = allocated = 0
    for path in (root, *root.rglob("*")):
        stat = path.lstat()
        identity = stat.st_dev, stat.st_ino
        if identity in seen:
            continue
        seen.add(identity)
        apparent += stat.st_size
        allocated += stat.st_blocks * 512
    return {"path": str(root), "apparent_bytes": apparent, "allocated_bytes": allocated, "unique_inodes": len(seen)}


def native_paths(lines):
    paths = set()
    for line in lines:
        values = line.split(maxsplit=5)
        if len(values) == 6 and values[5].startswith("/") and NATIVE.search(values[5]):
            path = Path(values[5])
            demand(path.is_file(), f"Native mapped file no longer available: {path}")
            paths.add(path.resolve())
    return paths


def elf_record(path, allowed):
    dynamic = subprocess.run(["readelf", "-d", str(path)], check=True, capture_output=True, text=True).stdout
    needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic)
    path_lists = re.findall(r"\((?:RUNPATH|RPATH)\).*?\[([^\]]+)\]", dynamic)
    demand(not any("/" in item or unsupported(Path(item)) for item in needed), f"Foreign DT_NEEDED in {path}")
    for path_list in path_lists:
        for entry in path_list.split(":"):
            origin = str(path.parent)
            target = entry.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)
            demand(entry and target.startswith("/") and allowed(Path(target).resolve()),
                   f"Unapproved search path in {path}: {entry}")
    paths = subprocess.run(["ldd", str(path)], check=True, capture_output=True, text=True).stdout
    demand("not found" not in paths, f"Unresolved library for {path}:\n{paths}")
    for row in paths.splitlines():
        found = re.search(r"(?:(?<= => )|^\s*)(/.*?)\s+\(0x", row)
        if found:
            library = Path(found[1]).resolve()
            demand(allowed(library) and not unsupported(library), f"Foreign dynamic dependency for {path}: {library}")
    return {"needed": needed, "runpaths": path_lists, "ldd": paths.splitlines()}


def environment_versions(executable):
    result = subprocess.run([executable, "-I", "-c", (
        "from importlib import metadata; import json, sys; "
        "print(json.dumps({'prefix':sys.prefix, 'versions':"
        "{d.metadata['Name']: d.version for d in metadata.distributions()}}))"
    )], check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    versions = {canonical_name(name): version for name, version in info["versions"].items()}
    demand(not BAD_PACKAGE & versions.keys(), "Installed environment contains ROOT bindings")
    return Path(info["prefix"]).resolve(), versions


def declared_gnu_runtime(files):
    paths = {path.resolve(strict=True) for path in files}
    demand(all(path.is_file() and path.name in {"libstdc++.so.6", "libgcc_s.so.1"}
               and not path.is_relative_to("/cvmfs") for path in paths),
           "Declare only local GCC runtime libstdc++.so.6 and libgcc_s.so.1 files")
    return {path: digest(path) for path in paths}


def inspect_install(bundle, wheels, runtime, cache, cpu, external_runtime):
    prefix = Path(runtime["prefix"]).resolve()
    base = Path(runtime["base_prefix"]).resolve()
    demand(prefix != base and runtime["python"].startswith("3.14."), "Expected an isolated CPython 3.14 environment")
    demand(runtime["threads"] == 1 and (cpu is None or runtime["cpu"] == [cpu]), "Process is outside the requested CPU/thread scope")
    env, versions = environment_versions(runtime["executable"])
    demand(env == prefix, "Install evidence reports an unexpected Python environment")
    unsupported_files = [str(path) for path in prefix.rglob("*") if unsupported(path)]
    demand(not unsupported_files, f"ROOT or framework files are present in the installed tree: {unsupported_files}")
    modules = {
        key: Path(runtime[key]).resolve() for key in
        ("correctionlib_module", "core_module", "campaign_module", "provider", "payload")
    }
    demand(all(path.is_relative_to(prefix) for path in modules.values()), "A module or payload is outside the installed environment")
    package = modules["campaign_module"].parent
    release = json.loads((package / "release.json").read_text(encoding="utf-8"))
    for key in ("provider", "payload"):
        demand(digest(modules[key]) == release[f"{key}_sha256"], f"Installed {key} differs from the receipt")
    demand(runtime["plugin_api"] == release["plugin_api"] == 4 and runtime["plugin_build_id"] == release["plugin_build_id"],
           "Live plugin handshake differs from the receipt")
    source_roles = package / "source_roles.json"
    demand(digest(source_roles) == release["source_roles_sha256"], "Installed source-role record differs from the receipt")
    for name, key in (("raw_input.py", "raw_reader_sha256"), ("workflow.json", "workflow_sha256")):
        demand(digest(package / name) == release[key], f"Installed {name} differs from the receipt")
    for name, pin in release["public_pins"].items():
        demand(versions.get(name) == pin, f"Installed dependency {name} differs from the resolved pin {pin}")

    core_wheel = bundle / "wheels" / release["core"]["file"]
    demand(digest(core_wheel) == release["core"]["sha256"], "Core wheel identity differs from the receipt")
    core_library = modules["correctionlib_module"].parent / "lib" / "libcorrectionlib.so"
    with zipfile.ZipFile(core_wheel) as archive:
        for path, member in (
            (modules["core_module"], f"correctionlib/{modules['core_module'].name}"),
            (core_library, "correctionlib/lib/libcorrectionlib.so"),
        ):
            demand(hashlib.sha256(archive.read(member)).hexdigest() == digest(path), f"Installed core bytes differ: {path}")
    for wheel in wheels.values():
        name = canonical_name(wheel["distribution"])
        demand(versions.get(name) == wheel["version"], f"Wrong installed wheel version: {name}")

    def allowed(path):
        return path in external_runtime or any(path.is_relative_to(root) for root in (
            prefix, base, Path("/lib").resolve(), Path("/lib64").resolve(),
            Path("/usr/lib").resolve(), Path("/usr/lib64").resolve(),
        ))

    active = native_paths(runtime["maps"])
    required = {modules["provider"], modules["core_module"], core_library.resolve()}
    demand(required <= active, "Wheel-owned provider and core did not all appear in live maps")
    demand(external_runtime.keys() <= active, "The declared compiler runtime did not appear in live maps")
    for path in active:
        demand(allowed(path) and not unsupported(path), f"Native load outside declared install or system: {path}")
    return {
        "python": runtime["python"], "executable": runtime["executable"], "threads": runtime["threads"], "cpu": runtime["cpu"],
        "versions": versions, "loaded_native_libraries": {str(path): digest(path) for path in sorted(active)},
        "elf_closure": {str(path): elf_record(path, allowed) for path in sorted(required)},
        "external_gnu_runtime_sha256": {str(path): external_runtime[path] for path in sorted(external_runtime)},
        "environment": scan_environment(prefix), "dependency_cache": scan_environment(cache),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runtime-record", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--cpu", type=int)
    parser.add_argument("--external-gnu-runtime", type=Path, action="append", default=[])
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    manifest, wheels = verify_bundle(bundle)
    evidence = {"bundle": manifest, "wheels": wheels}
    if args.runtime_record:
        runtime = json.loads(args.runtime_record.read_text(encoding="utf-8"))
        external = declared_gnu_runtime(args.external_gnu_runtime)
        evidence["installed"] = inspect_install(bundle, wheels, runtime, args.cache, args.cpu, external)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
