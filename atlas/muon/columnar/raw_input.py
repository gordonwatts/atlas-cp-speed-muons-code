"""Read persisted PHYSLITE branches and resolve event-local ElementLinks."""

from dataclasses import dataclass
import json
from pathlib import Path

import awkward as ak
import numpy as np
import uproot

MUON_PREFIX = "AnalysisMuonsAuxDyn."
EVENT_PREFIX = "EventInfoAuxDyn."
VERTEX_PREFIX = "PrimaryVerticesAuxDyn."
UINT32_MAX = 0xFFFFFFFF

MUON_FIELDS = (
    "pt", "eta", "phi", "charge", "muonType", "author", "allAuthors", "numberOfPrecisionLayers",
    "numberOfPrecisionHoleLayers", "numberOfGoodPrecisionLayers", "innerSmallHits", "innerLargeHits",
    "middleSmallHits", "middleLargeHits", "outerSmallHits", "outerLargeHits", "extendedSmallHits",
    "extendedLargeHits", "extendedSmallHoles", "isSmallGoodSectors", "etaLayer1STGCHits",
    "etaLayer2STGCHits", "MMHits", "combinedTrackOutBoundsPrecisionHits", "CaloMuonScore",
    "momentumBalanceSignificance", "scatteringCurvatureSignificance", "scatteringNeighbourSignificance",
    "ptvarcone30_Nonprompt_All_MaxWeightTTVA_pt500", "neflowisol20",
)
EVENT_FIELDS = (
    "eventNumber", "runNumber", "eventTypeBitmask", "RandomRunNumber",
    "beamPosSigmaX", "beamPosSigmaY", "beamPosSigmaXY",
)
PERIGEE_FIELDS = {"d0": "d0", "z0": "z0", "phi": "phi", "theta": "theta", "q_over_p": "qOverP"}
TRACK_SUMMARIES = {
    "pixel_hits": "numberOfPixelHits", "pixel_dead": "numberOfPixelDeadSensors",
    "sct_hits": "numberOfSCTHits", "sct_dead": "numberOfSCTDeadSensors",
    "pixel_holes": "numberOfPixelHoles", "sct_holes": "numberOfSCTHoles",
}
COV_DIAG = "definingParametersCovMatrixDiag"
COV_OFF = "definingParametersCovMatrixOffDiag"
MOMENTUM_ROLES = ("cb", "id", "me")
SELECTION_ROLES = ("id", "me", "cb", "msonly", "ms")


def load_workflow():
    path = Path(__file__).with_name("workflow.json")
    if not path.is_file():
        raise FileNotFoundError(f"Campaign input and state contract is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def open_source(path):
    return uproot.open(
        path, handler=uproot.source.file.MemmapSource, num_workers=1, use_threads=False,
        decompression_executor=uproot.TrivialExecutor(),
        interpretation_executor=uproot.TrivialExecutor(),
    )


def verify_file(file, tree, source):
    if str(file.file.uuid) != source["uuid"] or tree.num_entries != source["entries"]:
        raise ValueError("Input UUID or event count differs from the frozen PHYSLITE file")


def track_branch(collection, field):
    return f"{collection}AuxDyn.{field}"


def branch_plan(workflow, *, stored=False, observe=False):
    if stored:
        return (MUON_PREFIX + "pt",)

    branches = {MUON_PREFIX + field for field in MUON_FIELDS}
    branches.update(EVENT_PREFIX + field for field in EVENT_FIELDS)
    branches.update(VERTEX_PREFIX + field for field in ("vertexType", "z"))
    for role, link in workflow["links"].items():
        branches.add(MUON_PREFIX + link["branch"])
        for target in link["targets"].values():
            fields = set(PERIGEE_FIELDS.values()) | {"vz", COV_DIAG}
            if role in MOMENTUM_ROLES:
                fields.add(COV_OFF)
            branches.update(track_branch(target, field) for field in fields)
            branches.update(
                key for field in TRACK_SUMMARIES.values()
                if (key := track_branch(target, field)) in workflow["schema"]
            )
    if observe:
        branches.add(EVENT_PREFIX + "mcChannelNumber")
        branches.update(MUON_PREFIX + field for field in ("InnerDetectorPt", "MuonSpectrometerPt"))
    return tuple(sorted(branches))


def check_branches(tree, plan, schema):
    for key in plan:
        if key not in schema:
            raise ValueError(f"Branch {key!r} has no frozen source schema")
        actual = tree[key].typename
        if actual != schema[key]:
            raise TypeError(f"{key} is {actual!r}, expected {schema[key]!r}")


def numpy(array, dtype=None):
    return np.asarray(ak.to_numpy(array, allow_missing=False), dtype=dtype)


def flat(array, dtype=None):
    return numpy(ak.flatten(array, axis=1), dtype)


@dataclass(frozen=True)
class LinkRows:
    key: np.ndarray
    index: np.ndarray
    valid: np.ndarray
    locations: dict[str, tuple[np.ndarray, np.ndarray]]


class MuonChunk:
    """Muon fields and resolved track fields in original event and object order."""

    def __init__(self, columns, start, link_specs):
        self.columns = columns
        self.start = start
        self.link_specs = link_specs
        raw_pt = columns[MUON_PREFIX + "pt"]
        self.counts = numpy(ak.num(raw_pt, axis=1), np.int64)
        self.event_row = np.repeat(np.arange(len(self.counts), dtype=np.int64), self.counts)
        self.source_index = flat(ak.local_index(raw_pt, axis=1), np.uint32)
        self.muon_size = len(self.event_row)
        self.track_shapes = {}
        self.flat_tracks = {}
        self.links = {}

    def muon(self, field, dtype):
        key = MUON_PREFIX + field
        column = self.columns[key]
        if not np.array_equal(numpy(ak.num(column, axis=1)), self.counts):
            raise ValueError(f"{key} changed original muon multiplicity")
        return flat(column, dtype)

    def event(self, field, dtype):
        key = EVENT_PREFIX + field
        values = numpy(self.columns[key], dtype)
        if values.shape != self.counts.shape:
            raise ValueError(f"{key} does not have one value per original entry")
        return values

    def broadcast_event(self, field, dtype):
        return self.event(field, dtype)[self.event_row]

    def collection_shape(self, name):
        if name not in self.track_shapes:
            branch = track_branch(name, "qOverP")
            counts = numpy(ak.num(self.columns[branch], axis=1), np.int64)
            if counts.shape != self.counts.shape:
                raise ValueError(f"{branch} does not match the original event count")
            starts = np.cumsum(counts, dtype=np.int64) - counts
            self.track_shapes[name] = (counts, starts)
        return self.track_shapes[name]

    def link(self, role):
        if role in self.links:
            return self.links[role]

        spec = self.link_specs[role]
        name = MUON_PREFIX + spec["branch"]
        records = self.columns[name]
        key_field = records[name + ".m_persKey"]
        index_field = records[name + ".m_persIndex"]
        for field in (key_field, index_field):
            if not np.array_equal(numpy(ak.num(field, axis=1)), self.counts):
                raise ValueError(f"{name} changed original muon multiplicity")
        key = flat(key_field, np.uint32)
        index = flat(index_field, np.uint32)
        maybe_valid = (key != 0) & (index != UINT32_MAX)
        unknown = maybe_valid & ~np.isin(key, spec.get("unresolved_keys", ()))
        valid = np.zeros(self.muon_size, dtype=bool)
        locations = {}
        for persistent_key, collection in spec["targets"].items():
            matches = key == int(persistent_key)
            unknown &= ~matches
            counts, starts = self.collection_shape(collection)
            rows = np.flatnonzero(maybe_valid & matches & (index < counts[self.event_row]))
            indices = starts[self.event_row[rows]] + index[rows]
            valid[rows] = True
            locations[collection] = (rows, indices)
        if unknown.any():
            keys = np.unique(key[unknown]).tolist()
            raise ValueError(f"{name} contains unresolved persistent collection keys {keys} at entry {self.start}")
        result = LinkRows(key, index, valid, locations)
        self.links[role] = result
        return result

    def track_column(self, collection, field):
        branch = track_branch(collection, field)
        if branch not in self.flat_tracks:
            column = self.columns[branch]
            counts, _ = self.collection_shape(collection)
            if not np.array_equal(numpy(ak.num(column, axis=1)), counts):
                raise ValueError(f"{branch} changed the target track multiplicity")
            self.flat_tracks[branch] = ak.flatten(column, axis=1)
        return self.flat_tracks[branch]

    def track(self, role, field, dtype=np.float64, *, unavailable=0, optional=False):
        values = np.full(self.muon_size, unavailable, dtype=dtype)
        for collection, (rows, indices) in self.link(role).locations.items():
            if optional and track_branch(collection, field) not in self.columns:
                continue
            values[rows] = numpy(self.track_column(collection, field)[indices], dtype)
        return values

    def covariance(self, role, field, width):
        sizes = np.full(self.muon_size, -1, dtype=np.int64)
        values = np.zeros((self.muon_size, width), dtype=np.float64)
        for collection, (rows, indices) in self.link(role).locations.items():
            covariance = self.track_column(collection, field)[indices]
            sizes[rows] = numpy(ak.num(covariance, axis=1), np.int64)
            # Pass the original lengths separately. The node rejects malformed
            # used covariance lengths, including those longer than these slots.
            fixed = ak.fill_none(ak.pad_none(covariance, width, axis=1, clip=True), 0.0)
            values[rows] = numpy(fixed, np.float64)
        return sizes, values

    def choose_track(self, roles, values, dtype, unavailable):
        """Gather a raw field through the role code returned by CorrectionLib."""
        selected = np.full(self.muon_size, unavailable, dtype=dtype)
        if np.any((roles < 0) | (roles > len(SELECTION_ROLES))):
            raise ValueError("CorrectionLib returned an unknown track role")
        for i, name in enumerate(SELECTION_ROLES, 1):
            rows = roles == i
            if np.any(rows & ~self.link(name).valid):
                raise ValueError(f"CorrectionLib assigned the missing {name} track role")
            if rows.any():
                selected[rows] = values(name)[rows]
        return selected

    def jagged(self, values, dtype):
        return ak.unflatten(np.asarray(values, dtype=dtype), self.counts)
