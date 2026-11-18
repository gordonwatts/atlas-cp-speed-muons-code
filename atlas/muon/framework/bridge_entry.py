#!/usr/bin/env python3
"""Load the framework bridge only in the intended release Python process."""

import ctypes
import json
import os
from pathlib import Path
import resource
import runpy


class DlInfo(ctypes.Structure):
    _fields_ = (("filename", ctypes.c_char_p), ("base", ctypes.c_void_p), ("symbol", ctypes.c_char_p), ("address", ctypes.c_void_p))


def audit_paths(bridge):
    from release_symbols import SYMBOLS, exported_symbols

    symbols = {signature: mangled for mangled, signature in exported_symbols(bridge).items()}
    runtime = ctypes.CDLL(None)
    describe = runtime.dladdr
    describe.argtypes = (ctypes.c_void_p, ctypes.POINTER(DlInfo))
    describe.restype = ctypes.c_int
    records = {}
    for label, (_, signature) in SYMBOLS.items():
        address = ctypes.cast(getattr(runtime, symbols[signature]), ctypes.c_void_p)
        info = DlInfo()
        assert describe(address, ctypes.byref(info)) == 1 and info.filename
        owner = str(Path(os.fsdecode(info.filename)).resolve())
        records[label] = {"symbol": symbols[signature], "owner": owner, "address": hex(address.value)}
    mappings = sorted({line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines() if ".so" in line and line.split()[-1].startswith("/")})
    return {"bridge": str(bridge), "symbols": records, "mapped_libraries": mappings, "worker_threads": len(list(Path("/proc/self/task").iterdir())), "cpu_affinity": sorted(os.sched_getaffinity(0))}


if __name__ == "__main__":
    path = Path(os.environ["ATLAS_MUON_FRAMEWORK_BRIDGE"]).resolve(strict=True)
    audit = bool(os.environ.get("ATLAS_MUON_DISPATCH_AUDIT"))
    if audit:
        limit = 16 * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit if hard < 0 else min(limit, hard), hard))
    bridge = ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
    reference = Path(__file__).resolve().parents[3] / "tools/muon_reference/run_reference.py"
    if os.environ.get("ATLAS_MUON_STORED_CONTROL") == "1":
        reference = Path(os.environ["CP_RUNNER"])
    try:
        runpy.run_path(str(reference), run_name="__main__")
    finally:
        if audit:
            evidence = audit_paths(path)
            with Path("binding-map.json").open("x") as handle:
                json.dump(evidence, handle, indent=2)
                handle.write("\n")
