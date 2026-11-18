"""Collect installed-module and native-loader evidence for audit executions."""

import ctypes
import json
import os
from pathlib import Path
import sys


def record_runtime(destination, package, correctionlib=None, core=None, provider=None, payload=None):
    native = {}
    if provider is not None:
        # The provider was loaded earlier by CorrectionLib's ABI-4 handshake.
        library = ctypes.CDLL(str(provider))
        library.correctionlib_plugin_build_id.restype = ctypes.c_char_p
        library.correctionlib_plugin_api_version.restype = ctypes.c_int
        native = {
            "correctionlib_module": correctionlib.__file__, "core_module": core.__file__,
            "provider": str(provider), "payload": str(payload),
            "plugin_api": library.correctionlib_plugin_api_version(),
            "plugin_build_id": library.correctionlib_plugin_build_id().decode(),
        }
    with destination.open("x", encoding="utf-8") as handle:
        json.dump({
            "prefix": sys.prefix, "base_prefix": sys.base_prefix, "python": sys.version, "executable": sys.executable,
            "campaign_module": package.__file__, **native,
            "maps": Path("/proc/self/maps").read_text().splitlines(),
            "threads": len(list(Path("/proc/self/task").iterdir())), "cpu": sorted(os.sched_getaffinity(0)),
        }, handle, indent=2)
        handle.write("\n")
