"""Campaign histogram and original-object validation output."""

import json

import awkward as ak
import hist
import numpy as np
import uproot

from muon_analysis import CP_OK, READOUT_WRITE_BITS, TRACE_OUTPUTS
from raw_input import UINT32_MAX


class MuonHistogram:
    def __init__(self, states):
        axes = (
            hist.axis.Regular(100, 0, 500, underflow=True, overflow=True, name="pt_GeV"),
            hist.axis.StrCategory(states, growth=False, flow=False, name="systematic"),
        )
        self.states = states
        self.weighted = hist.Hist(*axes, storage=hist.storage.Weight())
        self.counts = hist.Hist(*axes, storage=hist.storage.Int64())

    def fill(self, state, pt_mev, weights=None):
        pt_gev = pt_mev.astype(np.float64, copy=False) / 1000.
        if not np.isfinite(pt_gev).all():
            raise ValueError(f"Non-finite selected momentum for {state}")
        if weights is not None and not np.isfinite(weights).all():
            raise ValueError(f"Non-finite selected weight for {state}")
        self.weighted.fill(pt_gev, state, weight=weights, threads=1)
        self.counts.fill(pt_gev, state, threads=1)

    def arrays(self):
        values = self.weighted.view(flow=True)
        return {
            "states": np.asarray(self.states), "pt_edges_GeV": self.weighted.axes[0].edges,
            "count": self.counts.view(flow=True), "sumw": values.value, "sumw2": values.variance,
        }

    def save(self, destination):
        with destination.open("xb") as output:
            np.savez_compressed(output, **self.arrays())


class ChunkObservations:
    def __init__(self, chunk, plan):
        self.chunk = chunk
        self.plan = plan
        eta = chunk.muon("eta", np.float32)
        phi = chunk.muon("phi", np.float32)
        self.columns = {
            "entry": np.arange(chunk.start, chunk.start + len(chunk.counts), dtype=np.uint64),
            "source_index": chunk.jagged(chunk.source_index, np.uint32),
            "eventNumber": chunk.event("eventNumber", np.uint64),
            "runNumber": chunk.event("runNumber", np.uint32),
            "mcChannelNumber": chunk.event("mcChannelNumber", np.uint32),
            "randomRunNumber": chunk.event("RandomRunNumber", np.uint32),
            "source_eta": chunk.jagged(eta, np.float32),
            "source_phi": chunk.jagged(phi, np.float32),
            "source_pt": chunk.jagged(chunk.muon("pt", np.float32), np.float32),
            "source_muonType": chunk.jagged(chunk.muon("muonType", np.uint16), np.uint16),
            "mu_eta": chunk.jagged(eta, np.float32), "mu_phi": chunk.jagged(phi, np.float32),
        }
        for role in chunk.link_specs:
            link = chunk.link(role)
            self.columns.update({
                f"source_link_{role}_key": chunk.jagged(link.key, np.uint32),
                f"source_link_{role}_index": chunk.jagged(link.index, np.uint32),
                f"source_link_{role}_valid": chunk.jagged(link.valid, np.bool_),
            })
        self.raw_detector_outputs = {
            "id_pt": chunk.muon("InnerDetectorPt", np.float32),
            "me_pt": chunk.muon("MuonSpectrometerPt", np.float32),
        }

    def add(self, state, result):
        def record(name, values, dtype):
            self.columns[f"{name}_{state}"] = self.chunk.jagged(values, dtype)

        mom = result.momentum
        selected = result.selection
        if state == result.kinematic_state:
            record("mu_pt", mom["pt"], np.float32)
            record("mu_e", mom["energy"], np.float32)
            record("mu_select_medium", selected["final"], np.int8)
            record("mu_detail_eta_gate", mom["eta_bits"], np.uint32)
            record("mu_detail_pt_gate", selected["pt_bits"], np.uint32)
            record("mu_detail_track_gate", selected["track_bits"], np.uint32)
            record("mu_detail_quality_gate", selected["medium"], np.int8)
            record("mu_detail_isolation_gate", selected["isolation"], np.int8)
            record("mu_detail_bad_muon", selected["bad_muon"], np.int8)
            record("mu_detail_calibration_valid", np.where(mom["status"] == CP_OK, UINT32_MAX, 0), np.uint32)
            record("mu_detail_charge", mom["charge"], np.float32)
            record("mu_detail_momentum_called", mom["called"], np.int8)
            record("mu_detail_momentum_status", mom["status"], np.int8)
            mask = mom["write_mask"].astype(np.int64)
            record("mu_detail_momentum_write_mask", mask, np.uint8)
            for field, bit in READOUT_WRITE_BITS.items():
                written = (mask & bit) != 0
                # A preserves existing ID/ME pT. New charge decorations have no
                # raw source field and the ntuple initializes the unwritten value.
                prior = self.raw_detector_outputs.get(field, 0.0)
                record(f"mu_detail_{field}", np.where(written, mom[field], prior), np.float32)
                record(f"mu_detail_{field}_written", written, np.bool_)
            for name in TRACE_OUTPUTS:
                if name in mom:
                    record(f"mu_momentum_{name}", mom[name], np.float64)

        record("mu_effSF_medium", result.combined_weight, np.float32)
        for name, weights in result.efficiency.items():
            if state == result.kinematic_state or self.plan.owners.get(state) == name:
                record(f"mu_{name}_effSF_medium", weights[:, 0], np.float32)
                record(f"mu_detail_{name}_sf_status", weights[:, 1], np.int32)
                record(f"mu_detail_{name}_sf_called", selected["final"], np.bool_)


class ObservationFile:
    def __init__(self, path, metadata):
        self.path = path
        self.metadata = metadata
        self.file = None
        self.tree = None

    def __enter__(self):
        self.file = uproot.create(self.path)
        self.file["columnar_metadata"] = json.dumps(self.metadata, separators=(",", ":"))
        return self

    def extend(self, columns):
        rows = ak.Array(columns)
        if self.tree is None:
            self.tree = self.file.mkrntuple("analysis", rows if len(rows) else rows.layout.form)
        elif len(rows):
            self.tree.extend(rows)

    def __exit__(self, _exc_type, _exc, _tb):
        if self.file is not None:
            self.file.close()


def branch_footprint(tree, plan):
    records = {}
    for key in plan:
        branch = tree[key]
        fields = [branch] if not branch.branches else branch.branches
        records[key] = {
            "typename": branch.typename,
            "baskets": sum(item.num_baskets for item in fields),
            "compressed_bytes_all_baskets": sum(item.compressed_bytes for item in fields),
            "uncompressed_bytes_all_baskets": sum(item.uncompressed_bytes for item in fields),
        }
    return records


def add_link_counts(chunks, data):
    for role in chunks.link_specs:
        link = chunks.link(role)
        for key in np.unique(link.key):
            rows = link.key == key
            selected_index = link.index[rows]
            record = data.setdefault(role, {}).setdefault(
                str(int(key)), {"valid": 0, "invalid": 0, "null": 0, "total": 0, "index_min": None, "index_max": None},
            )
            valid = int(link.valid[rows].sum())
            total = int(rows.sum())
            record["valid"] += valid
            record["invalid"] += total - valid
            record["null"] += int(((link.key == 0) & (link.index == UINT32_MAX) & rows).sum())
            record["total"] += total
            low, high = int(selected_index.min()), int(selected_index.max())
            record["index_min"] = low if record["index_min"] is None else min(record["index_min"], low)
            record["index_max"] = high if record["index_max"] is None else max(record["index_max"], high)
