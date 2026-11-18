#!/usr/bin/env bash
set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
columnar_venv=${1:?fresh virtual environment path required}
bootstrap=${2:?fresh tool and interpreter root required}
mkdir "$bootstrap"
export UV_CACHE_DIR="$bootstrap/cache"
export UV_PYTHON_INSTALL_DIR="$bootstrap/python"
export UV_PYTHON_BIN_DIR="$bootstrap/bin"
export UV_SYSTEM_CERTS=true
export UV_NO_PROGRESS=1

python3 - "$bootstrap" <<'PY'
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

destination = Path(sys.argv[1])
with urllib.request.urlopen("https://pypi.org/pypi/uv/0.13.9/json", timeout=30) as response:
    metadata = json.load(response)
wheel = next(wheel for wheel in metadata["urls"] if wheel["filename"].endswith("-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"))
with urllib.request.urlopen(wheel["url"], timeout=30) as response:
    data = response.read()
assert hashlib.sha256(data).hexdigest() == wheel["digests"]["sha256"]
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    binary = next(name for name in archive.namelist() if name.endswith(".data/scripts/uv"))
    (destination / "uv").write_bytes(archive.read(binary))
(destination / "uv").chmod(0o755)
(destination / "pypi-uv-wheel.json").write_text(json.dumps(wheel, indent=2) + "\n")
PY

uv="$bootstrap/uv"
"$uv" --version
"$uv" python install 3.14.10
"$uv" venv --python 3.14.10 "$columnar_venv"
"$uv" pip install --python "$columnar_venv/bin/python" -r "$reference_dir/requirements-reference.txt"
"$uv" pip freeze --python "$columnar_venv/bin/python" > "$columnar_venv/requirements-lock.txt"
"$uv" pip check --python "$columnar_venv/bin/python"
