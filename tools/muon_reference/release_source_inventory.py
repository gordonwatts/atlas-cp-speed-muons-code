#!/usr/bin/env python3
"""Record observable map schemas and resolve links with the original xAOD reflection."""

import json
import os
from pathlib import Path
import sys

import ROOT


LINK_FIELDS = (
    "combinedTrackParticleLink", "inDetTrackParticleLink", "extrapolatedMuonSpectrometerTrackParticleLink",
    "muonSpectrometerTrackParticleLink", "msOnlyExtrapolatedMuonSpectrometerTrackParticleLink",
)


def axis_description(axis):
    bins = axis.GetNbins()
    return {
        "title": axis.GetTitle(),
        "bins": bins,
        "edges": [axis.GetBinLowEdge(index) for index in range(1, bins + 2)],
        "labels": {index: axis.GetBinLabel(index) for index in range(1, bins + 1) if axis.GetBinLabel(index)},
    }


def inspect_maps(manifest):
    relevant = [entry for entry in manifest if "/Data22/" in entry["relative"] and "MuonMomentumCorrections/" in entry["relative"]]
    assert len(relevant) == 10
    result = []
    for entry in relevant:
        data = ROOT.TFile.Open(entry["path"])
        keys = []
        for key in data.GetListOfKeys():
            obj = key.ReadObj()
            item = {"name": key.GetName(), "class": key.GetClassName(), "cycle": key.GetCycle()}
            if obj.InheritsFrom("TH1"):
                axes = (obj.GetXaxis(), obj.GetYaxis(), obj.GetZaxis())[:obj.GetDimension()]
                item["axes"] = [axis_description(axis) for axis in axes]
                if obj.GetDimension() == 2:
                    nx, ny = obj.GetNbinsX(), obj.GetNbinsY()
                    samples = [(0, 0), (0, 1), (1, 0), (1, 1), (nx, ny), (nx + 1, 1), (1, ny + 1), (nx + 1, ny + 1)]
                    item["representative_bin_content_and_error"] = [[x, y, obj.GetBinContent(x, y), obj.GetBinError(obj.GetBin(x, y))] for x, y in samples]
                elif obj.GetDimension() == 1:
                    item["representative_bin_content_and_error"] = [[i, obj.GetBinContent(i), obj.GetBinError(i)] for i in (0, 1, obj.GetNbinsX(), obj.GetNbinsX() + 1)]
            if obj.InheritsFrom("TF1"):
                item["formula"] = str(obj.GetExpFormula())
                item["parameters"] = [obj.GetParameter(i) for i in range(obj.GetNpar())]
            keys.append(item)
        data.Close()
        result.append({"provenance": entry, "keys": keys})
    return result


def resolve_links(input_path):
    assert ROOT.xAOD.Init().isSuccess()
    source = ROOT.TFile.Open(input_path)
    tree = ROOT.xAOD.MakeTransientTree(source, "CollectionTree")
    persisted = source.Get("CollectionTree")
    getters = [
        name for name in LINK_FIELDS
        if persisted.GetBranch("AnalysisMuonsAuxDyn." + name)
    ]
    examples = []
    for entry in range(100):
        assert tree.GetEntry(entry) >= 0
        evt = tree.EventInfo
        for i, muon in enumerate(tree.AnalysisMuons):
            links = {}
            for name in getters:
                link = getattr(muon, name)()
                valid = link.isValid()
                links[name] = {"key": int(link.key()), "index": int(link.index()), "resolved_name": str(link.dataID()), "valid": bool(valid)}
            examples.append({"entry": entry, "muon_index": i, "muon_type": ord(muon.muonType()) if isinstance(muon.muonType(), str) else int(muon.muonType()), "event_number": int(evt.eventNumber()), "simulation_run_number": int(evt.runNumber()), "links": links})
            if len(examples) == 10:
                return examples
    raise RuntimeError("Fewer than 10 original muons in sample")


def resolve_all_links(input_path):
    assert ROOT.xAOD.Init().isSuccess()
    source = ROOT.TFile.Open(input_path)
    tree = ROOT.xAOD.MakeTransientTree(source, "CollectionTree")
    tree.SetBranchStatus("*", False)
    tree.SetBranchStatus("AnalysisMuons", True)
    categories = {name: {} for name in LINK_FIELDS}
    count = 0
    entries = int(tree.GetEntries())
    for entry in range(entries):
        assert tree.GetEntry(entry) >= 0
        for muon in tree.AnalysisMuons:
            count += 1
            for name in LINK_FIELDS:
                native = getattr(muon, name)()
                valid = bool(native.isValid())
                key = int(native.key())
                index = int(native.index())
                item = categories[name].setdefault(key, {"container_names": set(), "valid": 0, "invalid": 0, "null": 0, "total": 0})
                item["container_names"].add(str(native.dataID()))
                item["valid" if valid else "invalid"] += 1
                item["null"] += key == 0 and index == 0xFFFFFFFF
                item["total"] += 1
    for categories_for_link in categories.values():
        for item in categories_for_link.values():
            item["container_names"] = sorted(item["container_names"])
    return {"entries": entries, "muons": count, "link_fields": categories}


if __name__ == "__main__":
    result_directory = Path(sys.argv[1])
    if "--full-links" in sys.argv:
        output = resolve_all_links(os.environ["INPUT"])
        (result_directory / "native-full-link-keys.json").write_text(json.dumps(output, indent=2) + "\n")
    else:
        manifest = json.loads(Path(sys.argv[2]).read_text())
        output = {"momentum_maps": inspect_maps(manifest), "native_track_link_examples": resolve_links(os.environ["INPUT"])}
        (result_directory / "release-source-inventory.json").write_text(json.dumps(output, indent=2) + "\n")
