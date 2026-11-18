#!/usr/bin/env bash

set -eo pipefail
reference_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$reference_dir/environment.sh"
cd "$CAMPAIGN"
{
    hostname
    cat /etc/os-release
    taskset -pc $$
    ps -o ni,psr,nlwp,pid,comm -p $$
    lscpu
    command -v CPRun.py python root-config g++
    python --version
    root-config --version
    g++ --version
    printf 'AtlasVersion=%s\nAtlasProject=%s\nCMTCONFIG=%s\nLD_LIBRARY_PATH=%s\nDATAPATH=%s\n' "$AtlasVersion" "$AtlasProject" "$CMTCONFIG" "$LD_LIBRARY_PATH" "$DATAPATH"
    git -C correctionlib rev-parse HEAD
    git -C correctionlib status --short --branch
    git -C correctionlib ls-remote origin refs/heads/develop
    stat -L -c '%n %s %F' "$INPUT"
    readlink -f "$INPUT"
    sha256sum "$INPUT"
} | tee "$RESULTS/environment.log"
CPRun.py --help > "$RESULTS/cprun-help.txt"
python - <<'PY' | tee "$RESULTS/release-modules.txt"
import importlib.util
for module in ("MuonAnalysisAlgorithms.MuonAnalysisConfig", "AnalysisAlgorithmsConfig.ConfigAccumulator", "AnalysisAlgorithmsConfig.ConfigText", "AnaAlgorithm.DualUseConfig", "PyUtils.MetaReader"):
    spec = importlib.util.find_spec(module)
    print(module, spec.origin if spec else None)
PY
