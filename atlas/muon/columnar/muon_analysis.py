"""Columnar transport between original PHYSLITE and the shared muon corrections."""

from dataclasses import dataclass

import awkward as ak
import numpy as np

from raw_input import (
    COV_DIAG, COV_OFF, MOMENTUM_ROLES, PERIGEE_FIELDS, SELECTION_ROLES,
    TRACK_SUMMARIES, UINT32_MAX, VERTEX_PREFIX, flat, numpy,
)

MOMENTUM_OUTPUTS = (
    "called", "status", "write_mask", "pt", "eta", "phi", "energy", "charge",
    "id_pt", "me_pt", "id_charge", "me_charge",
)
TRACE_OUTPUTS = (
    "year_index", "root_seed", "g0", "g1", "g2", "g3", "g4", "g_high_pt",
    "raw_cb_gev", "raw_id_gev", "raw_me_gev", "expected_percent_res_id", "expected_percent_res_me",
    "expected_res_id", "expected_res_me", "scale_smear_called", "scale_cb_gev", "scale_id_gev",
    "scale_me_gev", "scale_cb_recombined_gev", "sagitta_called", "sagitta_direct_cb_gev",
    "sagitta_stat_comb_cb_gev", "sagitta_id_gev", "sagitta_me_gev", "sagitta_rho",
    "sagitta_stat_comb_status", "pre_override_cb_gev",
)
SELECTION_OUTPUTS = ("pt_bits", "track_bits", "medium", "bad_muon", "isolation", "final")
EFFICIENCY_TOOLS = {"reco": "muon_eff_reco", "isol": "muon_eff_iso", "TTVA": "muon_eff_ttva"}
EFFICIENCY_OWNERS = {"RECO": "reco", "ISO": "isol", "TTVA": "TTVA"}
SELECTION_MUON_INTS = {
    "author": "author", "all_authors": "allAuthors", "precision_layers": "numberOfPrecisionLayers",
    "precision_hole_layers": "numberOfPrecisionHoleLayers", "good_precision_layers": "numberOfGoodPrecisionLayers",
    "inner_small_hits": "innerSmallHits", "inner_large_hits": "innerLargeHits",
    "middle_small_hits": "middleSmallHits", "middle_large_hits": "middleLargeHits",
    "outer_small_hits": "outerSmallHits", "outer_large_hits": "outerLargeHits",
    "extended_small_hits": "extendedSmallHits", "extended_large_hits": "extendedLargeHits",
    "extended_small_holes": "extendedSmallHoles", "is_small_good_sectors": "isSmallGoodSectors",
    "eta_layer1_stgc_hits": "etaLayer1STGCHits", "eta_layer2_stgc_hits": "etaLayer2STGCHits",
    "mm_hits": "MMHits", "combined_out_of_bounds_hits": "combinedTrackOutBoundsPrecisionHits",
}
LOW_PT_FIELDS = ("momentumBalanceSignificance", "scatteringCurvatureSignificance", "scatteringNeighbourSignificance")
ISOLATION_FIELDS = ("ptvarcone30_Nonprompt_All_MaxWeightTTVA_pt500", "neflowisol20")
READOUT_WRITE_BITS = {"id_pt": 4, "me_pt": 8, "id_charge": 16, "me_charge": 32}
INT64 = np.int64
CP_OK = 2
CP_OOV = 1
SKIPPED = -1
IS_SIMULATION = 1


class Correction:
    def __init__(self, native, widths):
        self.native = native
        self.names = tuple(item.name for item in native.inputs)
        if native.output_size not in widths:
            raise ValueError(f"{native.name} output width {native.output_size} is incompatible with the campaign")

    def __call__(self, **fields):
        try:
            args = tuple(fields[name] for name in self.names)
        except KeyError as exc:
            raise ValueError(f"Missing {self.native.name} input {exc.args[0]}") from exc
        return self.native.evalv(*args)


@dataclass(frozen=True)
class StatePlan:
    states: tuple[str, ...]
    kinematic: frozenset[str]
    owners: dict[str, str]

    @classmethod
    def build(cls, workflow, full):
        state_lists = workflow["states"]
        states = tuple(state_lists["all_ordered"])
        kinematic = frozenset(state_lists["kinematic"])
        weights = frozenset(state_lists["weight_only"])
        if (
            len(states) != 97 or states[0] != "NOSYS" or len(set(states)) != len(states)
            or len(kinematic) != 28 or kinematic & weights or set(states[1:]) != kinematic | weights
        ):
            raise ValueError("Invalid frozen state order or coverage")
        owners = {name: EFFICIENCY_OWNERS[name.split("_", 3)[2]] for name in weights}
        return cls(states if full else ("NOSYS",), kinematic, owners)

    def kinematics_for(self, state):
        return state if state in self.kinematic else "NOSYS"

    def efficiency_for(self, state, tool):
        return state if self.owners.get(state) == tool else "NOSYS"


@dataclass(frozen=True)
class StateResult:
    kinematic_state: str
    momentum: dict
    selection: dict
    efficiency: dict
    combined_weight: np.ndarray


class NativeWorkflow:
    def __init__(self, providers, momentum_payload, selection_payload, efficiency_payload, observe):
        from correctionlib import _core, plugins

        for provider in providers:
            plugins.load_library(provider.resolve())
        paths = [path.resolve() for path in (momentum_payload, selection_payload, efficiency_payload)]
        payloads = {path: _core.CorrectionSet.from_file(str(path)) for path in dict.fromkeys(paths)}
        momentum, selection, efficiency = (payloads[path] for path in paths)
        trace_width = len(MOMENTUM_OUTPUTS) + len(TRACE_OUTPUTS)
        self.momentum = Correction(momentum["muon_momentum"], (12, trace_width) if observe else (12,))
        self.eta_gate = Correction(selection["muon_selection_eta"], (0,))
        self.vertex_kind = Correction(selection["muon_selection_vertex"], (0,))
        self.track_roles = Correction(selection["muon_selection_track_roles"], (2,))
        self.selection = Correction(selection["muon_selection_after_calibration"], (6,))
        self.efficiency = {name: Correction(efficiency[key], (2,)) for name, key in EFFICIENCY_TOOLS.items()}
        self.product = Correction(efficiency["muon_eff_product"], (0,))

    def apply(self, chunk, plan):
        """Yield results in frozen histogram order for a single original entry chunk."""
        common, selection_inputs = self.prepare(chunk)
        kinematics = {}
        efficiencies = {}
        for state in plan.states:
            kin_state = plan.kinematics_for(state)
            if kin_state not in kinematics:
                kinematics[kin_state] = self.calibrate(kin_state, common, selection_inputs)
            calibrated, selected = kinematics[kin_state]

            weights = {}
            for name, node in self.efficiency.items():
                source_state = plan.efficiency_for(state, name)
                key = name, kin_state, source_state
                if key not in efficiencies:
                    gate = selected["final"]
                    result = node(
                        state=source_state, random_run_number=common["random_run_number"],
                        pt=calibrated["pt"], eta=calibrated["eta"], phi=calibrated["phi"],
                        charge=calibrated["charge"], muon_type=common["muon_type"], gate=gate,
                    )
                    invalid = np.where(gate == 1, (result[:, 1] != CP_OK) & (result[:, 1] != CP_OOV),
                                       result[:, 1] != SKIPPED)
                    if invalid.any():
                        raise RuntimeError(f"Release {name} efficiency call/status mismatch for {state}")
                    efficiencies[key] = result
                weights[name] = efficiencies[key]

            combined = self.product(reco_sf=weights["reco"][:, 0], iso_sf=weights["isol"][:, 0],
                                    ttva_sf=weights["TTVA"][:, 0])
            yield state, StateResult(kin_state, calibrated, selected, weights, combined)

    def prepare(self, chunk):
        event_number = chunk.broadcast_event("eventNumber", np.uint64)
        eta = chunk.muon("eta", np.float64)
        eta_bits = self.eta_gate(raw_eta=eta).astype(INT64)
        muon_type = chunk.muon("muonType", INT64)
        bitmask = chunk.broadcast_event("eventTypeBitmask", INT64)
        event_info = {
            "run_number": chunk.broadcast_event("runNumber", INT64),
            "event_number_lo": (event_number & UINT32_MAX).astype(INT64),
            "event_number_hi": (event_number >> 32).astype(INT64),
            "random_run_number": chunk.broadcast_event("RandomRunNumber", INT64),
            "is_simulation": ((bitmask & IS_SIMULATION) != 0).astype(INT64),
        }
        common = {
            "called": (eta_bits == UINT32_MAX).astype(INT64),
            "muon_type": muon_type, "mu_pt": chunk.muon("pt", np.float64), "mu_eta": eta,
            "mu_phi": chunk.muon("phi", np.float64), "mu_charge": chunk.muon("charge", np.float64),
            **event_info,
        }

        links = {name: chunk.link(name).valid.astype(INT64) for name in SELECTION_ROLES}
        for role in MOMENTUM_ROLES:
            common[f"{role}_valid"] = links[role]
            for alias, stem in PERIGEE_FIELDS.items():
                common[f"{role}_{alias}"] = chunk.track(role, stem)
            for field, alias, width in ((COV_DIAG, "cov_diag", 5), (COV_OFF, "cov_off_diag", 10)):
                sizes, lanes = chunk.covariance(role, field, width)
                common[f"{role}_{alias}_size"] = sizes
                for i in range(width):
                    common[f"{role}_{alias}_{i}"] = lanes[:, i]

        roles = self.track_roles(muon_type=muon_type, **{f"{name}_link_valid": valid for name, valid in links.items()})
        selection = {
            "eta_bits": eta_bits, "muon_type": muon_type, "primary_role": roles[:, 0].astype(INT64),
            **{f"{key}_link_valid": links[key] for key in MOMENTUM_ROLES},
            **{key: chunk.muon(branch, INT64) for key, branch in SELECTION_MUON_INTS.items()},
        }
        selection.update(self.track_selection_inputs(chunk, selection["primary_role"], roles[:, 1].astype(INT64)))

        for role in MOMENTUM_ROLES:
            for field in ("q_over_p", "theta", "cov_diag_size"):
                selection[f"{role}_{field}"] = common[f"{role}_{field}"]
            selection[f"{role}_var_q_over_p"] = common[f"{role}_cov_diag_4"]
        selection["cb_phi"] = common["cb_phi"]

        track_iso, neutral_iso = (chunk.muon(field, np.float64) for field in ISOLATION_FIELDS)
        for branch in LOW_PT_FIELDS:
            chunk.muon(branch, np.float64)
        selection.update(
            track_iso=track_iso, neutral_iso=neutral_iso,
            isolation_available_mask=np.full(chunk.muon_size, (1 << len(ISOLATION_FIELDS)) - 1, dtype=INT64),
            lowpt_parameters_mask=np.full(chunk.muon_size, (1 << len(LOW_PT_FIELDS)) - 1, dtype=INT64),
            calo_score=chunk.muon("CaloMuonScore", np.float64),
            calo_score_available=np.ones(chunk.muon_size, dtype=INT64),
        )
        return common, selection

    def track_selection_inputs(self, chunk, primary, id_summary):
        vertices = chunk.columns[VERTEX_PREFIX + "vertexType"]
        z = chunk.columns[VERTEX_PREFIX + "z"]
        vertex_counts = numpy(ak.num(vertices, axis=1), INT64)
        if not np.array_equal(numpy(ak.num(z, axis=1)), vertex_counts):
            raise ValueError("Primary vertex columns disagree in shape")
        is_primary = self.vertex_kind(vertex_type=flat(vertices, INT64)) == 1
        primary_z = numpy(ak.fill_none(ak.firsts(z[ak.unflatten(is_primary, vertex_counts)]), 0.0), np.float64)
        primary_diag = {}
        for i, role in enumerate(SELECTION_ROLES, 1):
            if np.any(primary == i):
                primary_diag[role] = chunk.covariance(role, COV_DIAG, 5)
        return {
            **{f"primary_{name}": chunk.choose_track(
                primary, lambda role: chunk.track(role, name), np.float64, 0,
            ) for name in ("d0", "phi", "z0", "vz")},
            "primary_cov_diag_size": chunk.choose_track(primary, lambda role: primary_diag[role][0], INT64, -1),
            "primary_var_d0": chunk.choose_track(primary, lambda role: primary_diag[role][1][:, 0], np.float64, 0),
            **{name: chunk.choose_track(
                id_summary, lambda role: chunk.track(role, field, INT64, unavailable=-1, optional=True), INT64, -1,
            ) for name, field in TRACK_SUMMARIES.items()},
            "pv_z": primary_z[chunk.event_row],
            **{alias: chunk.broadcast_event(branch, np.float64) for alias, branch in (
                ("beam_sigma_x", "beamPosSigmaX"), ("beam_sigma_y", "beamPosSigmaY"), ("beam_sigma_xy", "beamPosSigmaXY"),
            )},
        }

    def calibrate(self, state, common, selection_inputs):
        momentum = self.momentum(systematic=state, **common)
        names = MOMENTUM_OUTPUTS + TRACE_OUTPUTS
        fields = {name: momentum[:, i] for i, name in enumerate(names[:momentum.shape[1]])}
        called = fields["called"] == 1
        if not np.array_equal(called, common["called"] == 1):
            raise RuntimeError(f"Momentum changed the calibration preselection for {state}")
        if np.any(np.where(called, fields["status"] != CP_OK, fields["status"] != SKIPPED)):
            raise RuntimeError(f"Release momentum abort policy for {state}")
        write_mask = fields["write_mask"].astype(INT64)
        corrected = self.selection(
            **selection_inputs, **{key: fields[key] for key in ("pt", "eta", "phi", "id_pt", "me_pt")},
            calibrated_track_momenta_available=((write_mask & 12) == 12).astype(INT64),
        )
        selection = {name: corrected[:, i].astype(INT64) for i, name in enumerate(SELECTION_OUTPUTS)}
        fields["eta_bits"] = selection_inputs["eta_bits"]
        return fields, selection
