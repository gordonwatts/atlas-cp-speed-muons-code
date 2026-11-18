"""Pack original objects and link data into typed, ordered CorrectionLib lanes."""

import numpy as np

from .raw_input import COV_DIAG, COV_OFF, MOMENTUM_ROLES, PERIGEE_FIELDS, SELECTION_ROLES, TRACK_SUMMARIES, UINT32_MAX

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

def order(correction, fields):
    """Read lanes in the loaded correction's declared positional order."""
    return tuple(fields[item.name] for item in correction.inputs)


def pack_momentum(chunk, eta_bits, muon_pt, muon_eta, muon_phi, muon_charge, muon_type, links):
    event_number = chunk.broadcast_event("eventNumber", np.uint64)
    flags = chunk.broadcast_event("eventTypeBitmask", np.int64)
    fields = {
        "called": (eta_bits == UINT32_MAX).astype(np.int64),
        "run_number": chunk.broadcast_event("runNumber", np.int64),
        "event_number_lo": (event_number & UINT32_MAX).astype(np.int64),
        "event_number_hi": (event_number >> 32).astype(np.int64),
        "is_simulation": ((flags & 1) != 0).astype(np.int64),
        "muon_type": muon_type, "mu_pt": muon_pt, "mu_eta": muon_eta, "mu_phi": muon_phi, "mu_charge": muon_charge,
    }
    for role in MOMENTUM_ROLES:
        fields[f"{role}_valid"] = links[role]
        for label, branch in PERIGEE_FIELDS.items():
            fields[f"{role}_{label}"] = chunk.track(role, branch)
        for field, label, width in ((COV_DIAG, "cov_diag", 5), (COV_OFF, "cov_off_diag", 10)):
            sizes, lanes = chunk.covariance(role, field, width)
            fields[f"{role}_{label}_size"] = sizes
            fields.update({f"{role}_{label}_{i}": lanes[:, i] for i in range(width)})
    return fields


def pack_selection(chunk, eta_bits, muon_type, links, track_roles, primary_vertex_z, momentum):
    primary = track_roles[:, 0].astype(np.int64)
    id_summary = track_roles[:, 1].astype(np.int64)
    fields = {
        "eta_bits": eta_bits, "muon_type": muon_type, "primary_role": primary,
        **{f"{name}_link_valid": links[name] for name in MOMENTUM_ROLES},
        **{key: chunk.muon(branch, np.int64) for key, branch in SELECTION_MUON_INTS.items()},
    }
    for role in MOMENTUM_ROLES:
        for name in ("q_over_p", "theta", "cov_diag_size"):
            fields[f"{role}_{name}"] = momentum[f"{role}_{name}"]
        fields[f"{role}_var_q_over_p"] = momentum[f"{role}_cov_diag_4"]
    fields["cb_phi"] = momentum["cb_phi"]

    primary_diag = {
        name: chunk.covariance(name, COV_DIAG, 5)
        for index, name in enumerate(SELECTION_ROLES, 1) if np.any(primary == index)
    }
    for name in ("d0", "phi", "z0", "vz"):
        fields[f"primary_{name}"] = chunk.choose_track(
            primary, lambda role: chunk.track(role, name), np.float64, 0,
        )
    fields["primary_cov_diag_size"] = chunk.choose_track(primary, lambda role: primary_diag[role][0], np.int64, -1)
    fields["primary_var_d0"] = chunk.choose_track(primary, lambda role: primary_diag[role][1][:, 0], np.float64, 0)
    for name, branch in TRACK_SUMMARIES.items():
        fields[name] = chunk.choose_track(
            id_summary, lambda role: chunk.track(role, branch, np.int64, unavailable=-1, optional=True),
            np.int64, -1,
        )
    fields["pv_z"] = primary_vertex_z[chunk.event_row]
    for label, branch in (("beam_sigma_x", "beamPosSigmaX"), ("beam_sigma_y", "beamPosSigmaY"), ("beam_sigma_xy", "beamPosSigmaXY")):
        fields[label] = chunk.broadcast_event(branch, np.float64)
    fields["track_iso"], fields["neutral_iso"] = (chunk.muon(field, np.float64) for field in ISOLATION_FIELDS)
    for branch in LOW_PT_FIELDS:
        chunk.muon(branch, np.float64)
    fields["isolation_available_mask"] = np.full(chunk.muon_size, (1 << len(ISOLATION_FIELDS)) - 1, dtype=np.int64)
    fields["lowpt_parameters_mask"] = np.full(chunk.muon_size, (1 << len(LOW_PT_FIELDS)) - 1, dtype=np.int64)
    fields["calo_score"] = chunk.muon("CaloMuonScore", np.float64)
    fields["calo_score_available"] = np.ones(chunk.muon_size, dtype=np.int64)
    return fields
