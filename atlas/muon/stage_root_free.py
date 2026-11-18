#!/usr/bin/env python3
"""Apply the reviewed ROOT-free overlay and reconcile the M11 parameter block."""

from pathlib import Path
import re
import shutil
import subprocess

import typer


def main(repository: Path, common: Path, rng: Path, header: Path, receipt: Path):
    chunks = re.split(r"(?m)(?=^(?:diff --git |@@ ))", common.read_text())
    selected = []
    adjusted = []
    for chunk in chunks:
        matrix_context = chunk.startswith("@@ ") and (
            "-    AngularVector tlv" in chunk or "-    tlv.SetCoordinates(id_corrected" in chunk
        )
        (adjusted if matrix_context else selected).append(chunk)
    assert len(adjusted) == 2
    path = repository / "atlas/muon/momentum/momentum.cxx"
    start = path.read_text()
    nominal = """    AngularVector tlv{id.pt, mu.eta, mu.phi, id.mass};
    auto inverse_momentum = [&]() { return tlv.P() == 0 ? 1e12 : 1.0 / (tlv.P() * 1e3); };
    id_parameters[4] = inverse_momentum();
    tlv.SetCoordinates(me.pt, mu.eta, mu.phi, me.mass);
    me_parameters[4] = inverse_momentum();"""
    corrected = """    tlv.SetCoordinates(id_corrected, mu.eta, mu.phi, id.mass);
    id_parameters[4] = inverse_momentum();
    tlv.SetCoordinates(me_corrected, mu.eta, mu.phi, me.mass);
    me_parameters[4] = inverse_momentum();"""
    assert start.count(nominal) == start.count(corrected) == 1
    assert "prepare_stat_combination" in start
    with receipt.open("x") as output:
        output.write("".join(selected))
    run = ["git", "-C", str(repository), "apply", "--recount"]
    subprocess.run([*run, "--check", str(receipt)], check=True)
    subprocess.run([*run, str(receipt)], check=True)
    new = path.read_text()
    assert new.count(nominal) == new.count(corrected) == 1
    new = new.replace(nominal, """    const auto inverse_momentum = [&](double pt) {
        const double p = root_compat::angular_momentum(pt, mu.eta);
        return p == 0 ? 1e12 : 1.0 / (p * 1e3);
    };
    id_parameters[4] = inverse_momentum(id.pt);
    me_parameters[4] = inverse_momentum(me.pt);""")
    new = new.replace(corrected, """    id_parameters[4] = inverse_momentum(id_corrected);
    me_parameters[4] = inverse_momentum(me_corrected);""")
    path.write_text(new)
    subprocess.run([*run, "--check", str(rng)], check=True)
    subprocess.run([*run, str(rng)], check=True)
    shutil.copyfile(header, path.with_name("root_compat.h"))


if __name__ == "__main__":
    typer.run(main)
