/*
 * Arithmetic adapted from Athena MuonMomentumCorrections at
 * 6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c.
 * Copyright (C) 2002-2026 CERN for the benefit of the ATLAS collaboration.
 */
#include "momentum.h"
#include "momentum_payload.h"
#include "root_compat.h"

#include "correction_plugin.h"

#include <Eigen/LU>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <numbers>
#include <optional>
#include <stdexcept>
#include <string_view>

namespace atlas::momentum {
namespace {

using Value = correction::Variable::Type;
using ValueType = correction::Variable::VarType;
using Parameters = Eigen::Matrix<double, 5, 1, 0, 5, 1>;
using Covariance = Eigen::Matrix<double, 5, 5, 0, 5, 5>;

constexpr auto gev_to_mev = 1e3;
constexpr auto mev_to_gev = 1e-3;
constexpr auto muon_mass_mev = 105.658367; // ParticleConstants uses PDG2011.
constexpr int combined_muon = 0;
constexpr int silicon_associated_forward_muon = 4;

// PATInterfaces/CorrectionCode.h. The skipped sentinel is transport only.
enum Status { skipped = -1, error = 0, out_of_validity = 1, ok = 2 };
enum WriteMask {
    write_pt = 1, write_charge = 2, write_id_pt = 4, write_me_pt = 8,
    write_id_charge = 16, write_me_charge = 32
};

enum Output {
    called_out, status_out, mask_out, pt_out, eta_out, phi_out, energy_out, charge_out,
    id_pt_out, me_pt_out, id_charge_out, me_charge_out,
    year_out, seed_out, g0_out, g1_out, g2_out, g3_out, g4_out, g_high_out,
    raw_cb_out, raw_id_out, raw_me_out, expected_percent_id_out, expected_percent_me_out,
    expected_id_out, expected_me_out, scale_called_out, scale_cb_out, scale_id_out,
    scale_me_out, scale_recombined_out, sagitta_called_out, sagitta_direct_cb_out,
    sagitta_stat_cb_out, sagitta_id_out, sagitta_me_out, sagitta_rho_out, sagitta_stat_status_out,
    pre_override_cb_out, output_count
};
constexpr auto production_output_count = static_cast<std::size_t>(year_out);

enum Input { state_in, called_in, run_in, event_lo_in, event_hi_in, simulation_in, type_in, pt_in,
             eta_in, phi_in, charge_in, prefix_size };
enum TrackInput { valid_in, d0_in, z0_in, track_phi_in, theta_in, qp_in, cov_diag_size_in,
                  cov_off_diag_size_in, cov_diag_in, cov_off_diag_in = cov_diag_in + 5,
                  track_input_size = cov_off_diag_in + 10 };
constexpr auto input_count = prefix_size + static_cast<std::size_t>(detector_count) * track_input_size;

constexpr std::array prefix_names{
    "systematic", "called", "run_number", "event_number_lo", "event_number_hi", "is_simulation",
    "muon_type", "mu_pt", "mu_eta", "mu_phi", "mu_charge"
};
constexpr std::array detector_names{"cb_", "id_", "me_"};
constexpr std::array track_names{
    "valid", "d0", "z0", "phi", "theta", "q_over_p", "cov_diag_size", "cov_off_diag_size"
};

struct Track {
    double raw_pt = 0;
    double pt = 0;
    int raw_charge = 0;
    int charge = 0;
    Parameters parameters = Parameters::Zero();
    Covariance covariance = Covariance::Zero();
};

struct Muon {
    std::array<Track, detector_count> tracks;
    double eta = 0;
    double phi = 0;
    std::array<double, 6> gaussian{};
    double expected_percent_id = 0;
    double expected_percent_me = 0;
    double expected_id = 0;
    double expected_me = 0;
    bool is_data = false;
    Period year = run4;
};

using GaussianDraws = std::array<double, 6>;
constexpr std::size_t rng_cache_slots = 8192;

struct GaussianCacheEntry {
    GaussianDraws draws;
    std::uint32_t seed;
    bool valid;
};

thread_local std::array<GaussianCacheEntry, rng_cache_slots> rng_cache{};

#ifdef ATLAS_MUON_M01_STATS
struct GaussianCacheCounts {
    std::uint64_t hits = 0;
    std::uint64_t misses = 0;
    std::uint64_t overwrites = 0;
    std::uint64_t zero_bypasses = 0;
};
thread_local GaussianCacheCounts rng_cache_counts{};
#endif

GaussianDraws draw_gaussians(std::uint32_t seed) {
    return root_compat::gaussians(seed);
}

GaussianDraws gaussians_for_seed(std::uint32_t seed) {
    if (seed == 0) {
#ifdef ATLAS_MUON_M01_STATS
        ++rng_cache_counts.zero_bypasses;
#endif
        // A zero key bypasses reuse and is rejected by the nonzero-seed implementation.
        return draw_gaussians(seed);
    }

    auto& entry = rng_cache[seed & (rng_cache_slots - 1)];
    if (entry.valid && entry.seed == seed) {
#ifdef ATLAS_MUON_M01_STATS
        ++rng_cache_counts.hits;
#endif
        return entry.draws;
    }
#ifdef ATLAS_MUON_M01_STATS
    ++rng_cache_counts.misses;
    if (entry.valid) ++rng_cache_counts.overwrites;
#endif
    entry.draws = draw_gaussians(seed);
    entry.seed = seed;
    entry.valid = true;
    return entry.draws;
}

std::uint32_t unsigned_limb(std::int64_t value) {
    if (value < 0 || value > std::numeric_limits<std::uint32_t>::max()) {
        throw std::invalid_argument("Momentum run/event limb must be in the unsigned 32-bit range");
    }
    return static_cast<std::uint32_t>(value);
}

Period get_period(bool is_data, std::uint32_t run) {
    // MuonCalibTool::getPeriod with useRandomRunNumber=false and no expert override.
    if (!is_data) {
        switch (run) {
            case 284500: return data16;
            case 300000: case 304000: case 305000: return data17;
            case 310000: return data18;
            case 330000: case 410000: return data22;
            case 450000: case 460000: return data23;
            case 470000: case 488000: case 488600: return data24;
            case 495000: case 500700: return data25;
            case 350000: case 350060: case 350140: case 350200: return run4;
            default: return run4;
        }
    }
    if (run < 320000) return data16;
    if (run <= 342000) return data17;
    if (run <= 370000) return data18;
    if (run <= 440613) return data22;
    if (run <= 461002) return data23;
    if (run <= 486894) return data24;
    if (run <= 524099) return data25;
    return run4;
}

Covariance read_covariance(const std::array<float, 5>& diag, const std::array<float, 10>& off,
                           std::int64_t diag_size, std::int64_t off_size) {
    // TrackingDetails::definingParametersCovMatrix and TrackHelpers' validity error.
    if (diag_size != 5 || (off_size != 6 && off_size != 10)) {
        throw std::runtime_error("DefiningParametersCovAccessor: track covariance matrix not available or improperly formatted");
    }
    Covariance result = Covariance::Zero();
    for (int i = 0; i < 5; ++i) result(i, i) = diag[i];
    auto fill = [&](int i, int j, float coefficient) {
        const float value = result(i, i) > 0 && result(j, j) > 0
            ? coefficient * std::sqrt(result(i, i) * result(j, j)) : 0;
        result(i, j) = result(j, i) = value;
    };
    if (off_size == 6) {
        constexpr std::array<std::pair<int, int>, 6> stored_pairs{{
            {0, 2}, {1, 3}, {0, 4}, {1, 4}, {2, 4}, {3, 4}
        }};
        for (std::size_t k = 0; k < stored_pairs.size(); ++k) {
            const auto [i, j] = stored_pairs[k];
            fill(i, j, off[k]);
        }
    } else {
        std::size_t k = 0;
        for (int i = 1; i < 5; ++i) {
            for (int j = 0; j < i; ++j, ++k) fill(i, j, off[k]);
        }
    }
    return result;
}

double track_pt(float q_over_p, float theta, float phi) {
    // TrackingDetails::genvecP4 performs each Cartesian projection in float.
    float p = 10.e6;
    if (std::fabs(q_over_p) > 0.) p = 1 / std::fabs(q_over_p);
    const float sin_theta = std::sin(theta);
    const float px = p * sin_theta * std::cos(phi);
    const float py = p * sin_theta * std::sin(phi);
    return root_compat::cartesian_pt(px, py);
}

double expected_resolution(const PeriodMaps& maps, const Muon& mu, Detector detector, bool add_mc_smearing) {
    const double pt = mu.tracks[detector].raw_pt;
    if (pt == 0) return 1e12;

    const auto expected = [&](ExpectedMap term) { return maps.expected[term].lookup(mu.eta, mu.phi); };
    const auto nominal_value = [&](ScaleParameter term) {
        return maps.scale_value(detector, term, nominal, mu.eta, mu.phi);
    };
    const auto combine = [&](double expected_value, ScaleParameter parameter) {
        if (!add_mc_smearing) return expected_value;
        return std::sqrt(std::pow(expected_value, 2) + std::pow(nominal_value(parameter), 2));
    };

    if (detector == ME) {
        const double p0 = combine(expected(me_r0), r0);
        const double p1 = combine(expected(me_r1), r1);
        const double p2 = combine(expected(me_r2), r2);
        return std::sqrt(std::pow(p0 / pt, 2) + std::pow(p1, 2) + std::pow(p2 * pt, 2));
    }

    const double p1 = combine(expected(id_r1), r1);
    double expected_p2 = expected(id_r2);
    const double p2_tan = expected(id_r2_tan2);
    if (p2_tan) expected_p2 = p2_tan;
    double p2 = combine(expected_p2, r2);
    if (add_mc_smearing && p2_tan) p2 = p2 * std::sinh(mu.eta) * std::sinh(mu.eta);
    return std::sqrt(std::pow(p1, 2) + std::pow(p2 * pt, 2));
}

using ScaleConstants = std::array<double, scale_parameter_count>;

bool varies_parameter(Action action, Detector detector, ScaleParameter parameter) {
    switch (action) {
        case Action::smear_cb: return detector == CB && parameter >= r0;
        case Action::smear_id: return detector == ID && parameter >= r0;
        case Action::smear_me: return detector == ME && parameter >= r0;
        case Action::scale_cb_s1: return detector == CB && parameter == s1;
        case Action::scale_cb_s0: return detector == CB && parameter == s0;
        case Action::scale_id: return detector == ID && parameter <= s1;
        case Action::scale_me_s1: return detector == ME && parameter == s1;
        case Action::scale_me_s0: return detector == ME && parameter == s0;
        default: return false;
    }
}

ScaleConstants scale_constants(const PeriodMaps& maps, const Muon& mu, Detector detector, State state) {
    ScaleConstants constants{};
    for (int i = 0; i < scale_parameter_count; ++i) {
        const auto parameter = static_cast<ScaleParameter>(i);
        double value = maps.scale_value(detector, parameter, nominal, mu.eta, mu.phi);
        if (varies_parameter(state.action, detector, parameter)) {
            if (state.direction > 0) {
                value += maps.scale_value(detector, parameter, up_error, mu.eta, mu.phi);
            } else {
                value -= maps.scale_value(detector, parameter, down_error, mu.eta, mu.phi);
            }
        }
        constants[i] = parameter >= r0 ? std::max(0.0, value) : value;
    }
    return constants;
}

double smear_correction(const Muon& mu, Detector detector, const ScaleConstants& c) {
    const double pt = mu.tracks[detector].pt;
    if (pt == 0) return 1;
    const auto& g = mu.gaussian;
    if (detector == ID) {
        double additional_weight = 1.;
        if (std::abs(mu.eta) > 2) additional_weight = std::sinh(mu.eta);
        return c[r1] * g[3] + c[r2] * g[4] * pt * additional_weight;
    }
    return c[r0] * g[0] / pt + c[r1] * g[1] + c[r2] * g[2] * pt;
}

double scale_smear_pt(const Muon& mu, Detector detector, const ScaleConstants& c) {
    const double pt = mu.tracks[detector].pt;
    if (pt == 0) return 0;
    const double smear = smear_correction(mu, detector, c);
    return (pt + pt * c[s1] + c[s0]) / (1 + smear);
}

// MuonCalibIntScaleSmearTool evaluates this path before writing track values.
// The value is recorded for diagnostics; correctData_CB writes the direct CB calibration.
double scale_smear_recombined(const Muon& mu, const ScaleConstants& id, const ScaleConstants& me) {
    const auto cb_pt = mu.tracks[CB].pt;
    const auto id_pt = mu.tracks[ID].pt;
    const auto me_pt = mu.tracks[ME].pt;
    const double delta_cb_me = cb_pt - me_pt;
    const double delta_cb_id = cb_pt - id_pt;
    double weight_id = 0.5;
    double weight_me = 0.5;

    if (me_pt == 0) {
        weight_id = 1.0;
        weight_me = 0.0;
    } else if (id_pt == 0) {
        weight_id = 0.0;
        weight_me = 1.0;
    } else if (cb_pt != 0 && (std::abs(delta_cb_me) > 0 || std::abs(delta_cb_id) > 0)) {
        if (std::abs(delta_cb_me) != 0 && std::abs(delta_cb_me) > std::abs(delta_cb_id)) {
            const double ratio = -delta_cb_id / delta_cb_me;
            const double sum = 1 + ratio;
            if (sum != 0 && ratio > 0) {
                weight_id = 1 / sum;
                weight_me = ratio / sum;
            }
        } else if (std::abs(delta_cb_id) != 0 && std::abs(delta_cb_me) < std::abs(delta_cb_id)) {
            const double ratio = -delta_cb_me / delta_cb_id;
            const double sum = 1 + ratio;
            if (sum != 0 && ratio > 0) {
                weight_id = ratio / sum;
                weight_me = 1 / sum;
            }
        }
    }

    constexpr double eps = std::numeric_limits<double>::epsilon();
    if (weight_id == 0.5 && weight_me == 0.5 &&
        !(mu.expected_percent_me < eps || cb_pt < eps || mu.expected_percent_id < eps)) {
        const double w_me = me_pt / cb_pt / std::pow(mu.expected_percent_me, 2);
        const double w_id = id_pt / cb_pt / std::pow(mu.expected_percent_id, 2);
        weight_id = w_id / (w_me + w_id);
        weight_me = w_me / (w_me + w_id);
    }

    const double id_smear = smear_correction(mu, ID, id);
    const double me_smear = smear_correction(mu, ME, me);
    const double smear = weight_id * id_smear + weight_me * me_smear;
    double scale_cb;
    if (weight_id == 0) scale_cb = (me[s1] + me[s0] / me_pt) * weight_me;
    else if (weight_me == 0) scale_cb = id[s1] * weight_id;
    else scale_cb = id[s1] * weight_id + (me[s1] + me[s0] / me_pt) * weight_me;
    return (cb_pt + cb_pt * scale_cb) / (1 + smear);
}

void apply_scale_smear(Muon& mu, const PeriodMaps& maps, State state, double* result) {
    const auto id = scale_constants(maps, mu, ID, state);
    const auto me = scale_constants(maps, mu, ME, state);
    const auto cb = scale_constants(maps, mu, CB, state);

    const double id_pt = scale_smear_pt(mu, ID, id);
    const double me_pt = scale_smear_pt(mu, ME, me);
    const double cb_pt = scale_smear_pt(mu, CB, cb);
    result[scale_recombined_out] = scale_smear_recombined(mu, id, me);
    const auto store = [](Track& track, double corrected_pt) {
        const int sign = corrected_pt < -0.1 ? -1 : 1;
        track.pt = corrected_pt * sign;
        track.charge = track.raw_charge * sign;
    };
    store(mu.tracks[ID], id_pt);
    store(mu.tracks[ME], me_pt);
    store(mu.tracks[CB], cb_pt);

    result[scale_called_out] = 1;
    result[scale_cb_out] = mu.tracks[CB].pt;
    result[scale_id_out] = mu.tracks[ID].pt;
    result[scale_me_out] = mu.tracks[ME].pt;
}

std::optional<double> sagitta_correction(const Payload& payload, const PeriodMaps& maps,
                                         const Muon& mu, Detector detector, State state) {
    const auto& histograms = maps.sagitta[detector];
    const auto value = [&](SagittaMap map) { return histograms[map].value().lookup(mu.eta, mu.phi); };
    if (mu.is_data) {
        return value(data_nominal) - value(mc_nominal);
    }

    const double eta = mu.eta;
    switch (state.action) {
        case Action::poseta:
        case Action::negeta: {
            double correction = value(data_residual);
            correction *= state.direction;
            if ((state.action == Action::poseta && eta < 0) ||
                (state.action == Action::negeta && eta > 0)) correction = 0;
            return correction;
        }
        case Action::global: {
            double deltas = payload.extra_rebias;
            // The initialized release is Recs2025_12_11_Run2Run3.
            if (mu.year == data22 && std::abs(eta) > 1.05) deltas = 1.5 * deltas;
            return deltas * state.direction;
        }
        case Action::ptextra: {
            const double pt = mu.tracks[detector].pt;
            const double distance = std::abs((pt > 450. ? 450. : pt) - 45);
            double correction = 0;
            if (histograms[data_pt_extra]) {
                correction += distance * value(data_pt_extra);
            } else {
                const double deltas = 0.00002;
                if (eta > 2 || (eta > -2 && eta < -1.05)) correction += distance / 100 * deltas;
                if (eta < -2 || (eta < 2 && eta > 1.5)) correction += distance / 200 * deltas;
                if (mu.year == data22) {
                    if (eta > -2 && eta < -1.05) correction *= 2.5;
                    if (eta < -2) correction *= 6;
                    if (eta > 1.5 && eta < 2) correction += distance / 80 * deltas;
                    if (eta > 1.05 && eta < 1.5) correction += distance / 40 * deltas;
                }
            }
            return correction * state.direction;
        }
        case Action::datastat: {
            double correction = value(data_stat);
            correction *= 0.5 * state.direction;
            return correction;
        }
        default:
            return std::nullopt;
    }
}

double sagitta_pt(const Muon& mu, Detector detector, std::optional<double> correction) {
    const Track& track = mu.tracks[detector];
    double pt = track.pt;
    if (correction) {
        if (mu.is_data) pt = pt / (1 + track.charge * *correction * pt);
        else pt = pt / (1 - track.charge * *correction * pt);
    }
    return pt;
}

struct StatCombinationWeights {
    Covariance id;
    Covariance me;
    Covariance combined;
    Covariance inverse_sum;
};

Status prepare_stat_combination(const Covariance& id_cov, const Covariance& me_cov,
                                StatCombinationWeights& weights) {
    // MuonCalibIntSagittaTool::applyStatCombination, using the same Eigen decomposition.
    const Eigen::FullPivLU<Covariance> id_lu(id_cov);
    if (!id_lu.isInvertible()) return error;
    weights.id = id_lu.inverse();
    const Eigen::FullPivLU<Covariance> me_lu(me_cov);
    if (!me_lu.isInvertible()) return error;
    weights.me = me_lu.inverse();
    const Eigen::FullPivLU<Covariance> combined_lu(weights.id + weights.me);
    if (!combined_lu.isInvertible()) return error;
    weights.combined = combined_lu.inverse();
    const Eigen::FullPivLU<Covariance> sum_lu(id_cov + me_cov);
    if (!sum_lu.isInvertible()) return error;
    weights.inverse_sum = sum_lu.inverse();
    return ok;
}

void combine_stat_parameters(const Parameters& id, const Parameters& me, int charge,
                             const StatCombinationWeights& weights, Parameters& combined) {
    // Retain the source chi2 calculation for its numerical domain.
    const Parameters difference = id - me;
    [[maybe_unused]] double chi2 = difference.transpose() * weights.inverse_sum * difference;
    chi2 = chi2 / 5.;

    combined = weights.combined * (weights.id * id + weights.me * me);
    combined[4] *= charge;
    if (combined[2] > std::numbers::pi) combined[2] -= 2. * std::numbers::pi;
    else if (combined[2] < -std::numbers::pi) combined[2] += 2. * std::numbers::pi;
}

double stat_combination(const Muon& mu, double id_corrected, double me_corrected, double& status) {
    const Track& cb = mu.tracks[CB];
    const Track& id = mu.tracks[ID];
    const Track& me = mu.tracks[ME];
    if (id.pt == 0 || me.pt == 0 || id_corrected == 0 || me_corrected == 0) {
        status = skipped;
        return cb.pt;
    }

    Parameters id_parameters = id.parameters;
    Parameters me_parameters = me.parameters;

    const auto inverse_momentum = [&](double pt) {
        const double p = root_compat::angular_momentum(pt, mu.eta);
        return p == 0 ? 1e12 : 1.0 / (p * 1e3);
    };
    id_parameters[4] = inverse_momentum(id.pt);
    me_parameters[4] = inverse_momentum(me.pt);

    id_parameters[4] = std::abs(id_parameters[4]);
    me_parameters[4] = std::abs(me_parameters[4]);
    StatCombinationWeights weights;
    status = prepare_stat_combination(id.covariance, me.covariance, weights);
    if (status != ok) return cb.pt;

    Parameters cb_nominal;
    combine_stat_parameters(id_parameters, me_parameters, cb.charge, weights, cb_nominal);

    id_parameters[4] = inverse_momentum(id_corrected);
    me_parameters[4] = inverse_momentum(me_corrected);

    id_parameters[4] = std::abs(id_parameters[4]);
    me_parameters[4] = std::abs(me_parameters[4]);
    Parameters cb_corrected;
    combine_stat_parameters(id_parameters, me_parameters, cb.charge, weights, cb_corrected);

    const double pt_nominal = std::sin(cb_nominal[3]) / std::abs(cb_nominal[4]);
    const double pt_corrected = std::sin(cb_corrected[3]) / std::abs(cb_corrected[4]);
    return cb.pt * (pt_corrected / pt_nominal);
}

void apply_sagitta(Muon& mu, const Payload& payload, const PeriodMaps& maps, State state, double* result) {
    const auto id_correction = sagitta_correction(payload, maps, mu, ID, state);
    const auto me_correction = sagitta_correction(payload, maps, mu, ME, state);
    const auto cb_correction = sagitta_correction(payload, maps, mu, CB, state);
    const double id_pt = sagitta_pt(mu, ID, id_correction);
    const double me_pt = sagitta_pt(mu, ME, me_correction);
    const double cb_direct = sagitta_pt(mu, CB, cb_correction);
    const double cb_stat = stat_combination(mu, id_pt, me_pt, result[sagitta_stat_status_out]);
    // The RHO nuisance computes the shifted center before the fixed-rho choice.
    [[maybe_unused]] double central = 45.2;
    if (state.action == Action::rho) {
        const double sigma_id = mu.expected_id;
        const double sigma_me = mu.expected_me;
        const double denominator = mu.tracks[CB].pt * std::sqrt(sigma_id * sigma_id + sigma_me * sigma_me);
        const double resolution = denominator ? std::numbers::sqrt2 * sigma_id * sigma_me / denominator : 0.;
        if (state.direction > 0) central += std::abs(0.5 * resolution * central);
        else central -= std::abs(0.5 * resolution * central);
    }
    const double rho = payload.fixed_rho;
    // Even at rho=1 the source evaluates 0 * cb_stat; retain its NaN behavior.
    mu.tracks[CB].pt = rho * cb_direct + (1 - rho) * cb_stat;
    mu.tracks[ID].pt = id_pt;
    mu.tracks[ME].pt = me_pt;
    result[sagitta_called_out] = 1;
    result[sagitta_direct_cb_out] = cb_direct;
    result[sagitta_stat_cb_out] = cb_stat;
    result[sagitta_id_out] = id_pt;
    result[sagitta_me_out] = me_pt;
    result[sagitta_rho_out] = rho;
}

class MomentumNode final : public correction::PluginNode {
public:
    MomentumNode(const correction::PluginConfig& config, const correction::PluginContext& context)
        : payload_(config), result_size_(context.output_size()) {
        for (std::size_t i = 0; i < prefix_names.size(); ++i) {
            const auto type = i == state_in ? ValueType::string : (i < pt_in ? ValueType::integer : ValueType::real);
            indices_[i] = context.input_index(prefix_names[i], type);
        }
        for (std::size_t detector = 0; detector < detector_names.size(); ++detector) {
            const auto start = prefix_size + detector * track_input_size;
            const std::string name = detector_names[detector];
            for (std::size_t i = 0; i < track_names.size(); ++i) {
                const auto type = i == valid_in || i >= cov_diag_size_in ? ValueType::integer : ValueType::real;
                indices_[start + i] = context.input_index(name + track_names[i], type);
            }
            for (std::size_t i = 0; i < 5; ++i) {
                indices_[start + cov_diag_in + i] =
                    context.input_index(name + "cov_diag_" + std::to_string(i), ValueType::real);
            }
            for (std::size_t i = 0; i < 10; ++i) {
                indices_[start + cov_off_diag_in + i] =
                    context.input_index(name + "cov_off_diag_" + std::to_string(i), ValueType::real);
            }
        }
    }

    std::size_t output_size() const override { return result_size_; }

    double evaluate(const std::vector<Value>&) const override {
        throw std::logic_error("muon_momentum requires fixed array evaluation");
    }

    void evaluate_array(const std::vector<Value>& values, double* result, std::size_t size) const override {
        if (!result || size < result_size_) throw std::invalid_argument("Momentum output buffer is too short");
        if (result_size_ == output_count) {
            evaluate_impl(values, result);
        } else {
            std::array<double, output_count> diagnostics;
            evaluate_impl(values, diagnostics.data());
            std::copy_n(diagnostics.data(), result_size_, result);
        }
    }

private:
    void evaluate_impl(const std::vector<Value>& values, double* result) const {
        std::fill_n(result, output_count, std::numeric_limits<double>::quiet_NaN());

        const State state = payload_.states.at(std::get<std::string>(values[indices_[state_in]]));
        const auto integer = [&](std::size_t pos) { return std::get<std::int64_t>(values[indices_[pos]]); };
        const auto real = [&](std::size_t pos) { return static_cast<float>(std::get<double>(values[indices_[pos]])); };
        const auto boolean = [&](std::size_t pos) {
            const auto value = integer(pos);
            if (value != 0 && value != 1) throw std::invalid_argument("Momentum flags must be 0 or 1");
            return value == 1;
        };
        const bool called = boolean(called_in);

        result[called_out] = called;
        result[status_out] = skipped;
        result[mask_out] = 0;
        result[scale_called_out] = 0;
        result[sagitta_called_out] = 0;
        result[sagitta_stat_status_out] = skipped;
        result[pt_out] = real(pt_in);
        result[eta_out] = real(eta_in);
        result[phi_out] = real(phi_in);
        result[charge_out] = real(charge_in);

        if (called) {
            const bool is_data = !boolean(simulation_in);
            const auto run = unsigned_limb(integer(run_in));
            const auto event = (static_cast<std::uint64_t>(unsigned_limb(integer(event_hi_in))) << 32)
                | unsigned_limb(integer(event_lo_in));
            const auto type = integer(type_in);
            if (type < 0 || type > 5) throw std::invalid_argument("Invalid xAOD muon type");
            int charge = static_cast<int>(real(charge_in));
            if (type == silicon_associated_forward_muon && boolean(static_cast<std::size_t>(prefix_size) + valid_in)) {
                const float q_over_p = real(static_cast<std::size_t>(prefix_size) + qp_in);
                charge = q_over_p > 0 ? 1 : (q_over_p < 0 ? -1 : 0);
            }

            Muon mu;
            mu.eta = result[eta_out];
            mu.phi = result[phi_out];
            mu.year = get_period(is_data, run);
            mu.is_data = is_data;
            result[year_out] = mu.year;
            for (std::size_t detector = 0; detector < detector_count; ++detector) {
                const auto start = prefix_size + detector * track_input_size;
                auto& track = mu.tracks[detector];
                track.raw_charge = track.charge = charge;
                if (!boolean(start + valid_in)) continue;

                const auto phi = real(start + track_phi_in);
                const auto theta = real(start + theta_in);
                const auto q_over_p = real(start + qp_in);
                track.parameters << real(start + d0_in), real(start + z0_in), phi, theta, q_over_p;
                track.raw_pt = track.pt = track_pt(q_over_p, theta, phi) * mev_to_gev;
                std::array<float, 5> diag;
                std::array<float, 10> off;
                for (std::size_t i = 0; i < diag.size(); ++i) diag[i] = real(start + cov_diag_in + i);
                for (std::size_t i = 0; i < off.size(); ++i) off[i] = real(start + cov_off_diag_in + i);
                track.covariance = read_covariance(diag, off, integer(start + cov_diag_size_in),
                                                   integer(start + cov_off_diag_size_in));
            }

            result[raw_cb_out] = mu.tracks[CB].raw_pt;
            result[raw_id_out] = mu.tracks[ID].raw_pt;
            result[raw_me_out] = mu.tracks[ME].raw_pt;

            const auto seed = root_compat::muon_seed(mu.phi, mu.eta, event);
            mu.gaussian = gaussians_for_seed(seed);
            result[seed_out] = seed;
            for (std::size_t i = 0; i < mu.gaussian.size(); ++i) {
                result[g0_out + i] = mu.gaussian[i];
            }

            const auto& maps = payload_.periods[mu.year];
            mu.expected_percent_id = expected_resolution(maps, mu, ID, false);
            mu.expected_percent_me = expected_resolution(maps, mu, ME, false);
            mu.expected_id = expected_resolution(maps, mu, ID, true) * mu.tracks[CB].pt;
            mu.expected_me = expected_resolution(maps, mu, ME, true) * mu.tracks[CB].pt;
            result[expected_percent_id_out] = mu.expected_percent_id;
            result[expected_percent_me_out] = mu.expected_percent_me;
            result[expected_id_out] = mu.expected_id;
            result[expected_me_out] = mu.expected_me;

            if (!is_data) apply_scale_smear(mu, maps, state, result);
            if (is_data || type != silicon_associated_forward_muon) apply_sagitta(mu, payload_, maps, state, result);

            auto& cb = mu.tracks[CB];
            const auto& id = mu.tracks[ID];
            const auto& me = mu.tracks[ME];
            result[pre_override_cb_out] = cb.pt;
            if (std::abs(me.pt) == 0) cb.pt = id.pt;
            if (std::abs(id.pt) == 0) cb.pt = me.pt;

            int mask = write_id_pt | write_me_pt | write_id_charge | write_me_charge;
            if (!is_data || type == combined_muon) {
                mask |= write_pt;
                result[pt_out] = static_cast<float>(cb.pt * gev_to_mev);
            }
            if (!is_data) {
                mask |= write_charge;
                result[charge_out] = static_cast<float>(cb.charge);
            }
            result[mask_out] = mask;
            result[id_pt_out] = static_cast<float>(id.pt * gev_to_mev);
            result[me_pt_out] = static_cast<float>(me.pt * gev_to_mev);
            result[id_charge_out] = static_cast<float>(id.charge);
            result[me_charge_out] = static_cast<float>(me.charge);
            result[status_out] = ok;
        }

        result[energy_out] = root_compat::positive_mass_energy(result[pt_out], result[eta_out], muon_mass_mev);
    }

    const Payload payload_;
    const std::size_t result_size_;
    std::array<std::size_t, input_count> indices_{};
};

} // namespace
} // namespace atlas::momentum

void register_momentum(correction::PluginRegistry& registry) {
    static_assert(correction::plugin_api_version == 4);
    registry.register_node("atlas.muon_momentum", 1,
        [](const correction::PluginConfig& config, const correction::PluginContext& context) {
            const auto width = context.output_size();
            if (context.child_count() != 0 ||
                (width != atlas::momentum::production_output_count && width != atlas::momentum::output_count)) {
                throw std::invalid_argument("atlas.muon_momentum requires a version 1 fixed output and no children");
            }
            return std::make_shared<atlas::momentum::MomentumNode>(config, context);
        });
}

#ifdef ATLAS_MUON_M01_STATS
// Test-only symbols. Each call observes the calling thread's table.
extern "C" void atlas_muon_m01_rng_cache_counts(std::uint64_t* output) {
    const auto& counts = atlas::momentum::rng_cache_counts;
    output[0] = counts.hits;
    output[1] = counts.misses;
    output[2] = counts.overwrites;
    output[3] = counts.zero_bypasses;
}

extern "C" void atlas_muon_m01_rng_cache_reset() {
    for (auto& entry : atlas::momentum::rng_cache) entry.valid = false;
    atlas::momentum::rng_cache_counts = {};
}

extern "C" std::size_t atlas_muon_m01_rng_cache_bytes() {
    return sizeof(atlas::momentum::rng_cache);
}

extern "C" void atlas_muon_m01_rng_probe_seed(std::uint32_t seed, double* output) {
    try {
        const auto draws = atlas::momentum::gaussians_for_seed(seed);
        std::copy(draws.begin(), draws.end(), output);
    } catch (const std::domain_error&) {
        std::fill_n(output, 6, std::numeric_limits<double>::quiet_NaN());
    }
}
#endif
