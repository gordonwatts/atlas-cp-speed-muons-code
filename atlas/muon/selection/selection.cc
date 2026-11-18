#include "selection.h"
#include "correction_plugin.h"

#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>

namespace {
using correction::PluginConfig;
using correction::PluginContext;
using correction::PluginNode;
using correction::Variable;
using Type = Variable::VarType;
using Values = std::vector<Variable::Type>;

constexpr uint32_t accepted = std::numeric_limits<uint32_t>::max();
constexpr uint32_t rejected = accepted ^ 1;
constexpr uint32_t track_blank = accepted ^ 7;

enum MuonType { combined, standalone, segment_tagged, calo_tagged, silicon_forward, zero_pixel };
enum Author { muid_co = 1, staco = 2, mu_tag_imo = 4, muid_sa = 5, mu_girl = 6 };
enum TrackRole { no_track, id_track, me_track, cb_track, msonly_track, ms_track };
enum class Stage { all, pt, track, quality, isolation };

Stage configured_stage(const PluginConfig& config) {
    const auto stage = config.string("stage");
    if (stage == "all") return Stage::all;
    if (stage == "pt") return Stage::pt;
    if (stage == "track") return Stage::track;
    if (stage == "quality") return Stage::quality;
    if (stage == "isolation") return Stage::isolation;
    throw std::invalid_argument("Unknown muon selection stage");
}

struct StageInputs {
    const PluginContext& context;
    bool active;

    size_t input_index(const std::string& name, Type type) const {
        return active ? context.input_index(name, type) : 0;
    }
};

void require_shape(const PluginContext& context, size_t size) {
    if (context.output_size() != size || context.child_count() != 0) {
        throw std::invalid_argument("Unexpected selection node shape or children");
    }
}

struct Row {
    const Values& values;

    float stored(size_t index) const { return static_cast<float>(std::get<double>(values[index])); }
    int64_t integer(size_t index) const { return std::get<int64_t>(values[index]); }

    bool flag(size_t index) const {
        const auto value = integer(index);
        if (value != 0 && value != 1) {
            throw std::invalid_argument("Expected selection availability 0 or 1");
        }
        return value == 1;
    }

    int hit(size_t index) const {
        const auto value = integer(index);
        if (value < 0 || value > std::numeric_limits<uint8_t>::max()) {
            throw std::runtime_error("Required xAOD hit summary is unavailable or outside uint8");
        }
        return static_cast<int>(value);
    }
};

class EtaSelection final : public PluginNode {
public:
    EtaSelection(const PluginConfig& config, const PluginContext& context)
        : eta_(context.input_index("raw_eta", Type::real)),
          max_eta_(static_cast<float>(config.real("max_eta"))) {
        config.require_keys({"max_eta"});
        require_shape(context, 0);
    }

    double evaluate(const Values& values) const override {
        return std::abs(Row{values}.stored(eta_)) <= max_eta_ ? accepted : rejected;
    }

private:
    size_t eta_;
    float max_eta_;
};

class TrackRoles final : public PluginNode {
public:
    TrackRoles(const PluginConfig& config, const PluginContext& context)
        : muon_type_(context.input_index("muon_type", Type::integer)),
          id_(context.input_index("id_link_valid", Type::integer)),
          me_(context.input_index("me_link_valid", Type::integer)),
          cb_(context.input_index("cb_link_valid", Type::integer)),
          msonly_(context.input_index("msonly_link_valid", Type::integer)),
          ms_(context.input_index("ms_link_valid", Type::integer)) {
        config.require_keys({});
        require_shape(context, 2);
    }

    double evaluate(const Values&) const override {
        throw std::invalid_argument("Track role selection has two output components");
    }

    size_t output_size() const override { return 2; }

    void evaluate_array(const Values& values, double* output, size_t size) const override {
        if (size != output_size()) {
            throw std::invalid_argument("Track role selection requires two output components");
        }
        const Row row{values};
        const auto type = row.integer(muon_type_);
        TrackRole primary = no_track;
        switch (type) {
        case combined:
        case silicon_forward:
            if (row.flag(cb_)) primary = cb_track;
            break;
        case segment_tagged:
        case calo_tagged:
            if (row.flag(id_)) primary = id_track;
            break;
        case standalone:
        case zero_pixel:
            if (row.flag(me_)) primary = me_track;
            else if (row.flag(msonly_)) primary = msonly_track;
            else if (row.flag(ms_)) primary = ms_track;
            break;
        default:
            break;
        }
        output[0] = primary;
        output[1] = row.flag(id_) ? id_track : primary;
    }

private:
    size_t muon_type_, id_, me_, cb_, msonly_, ms_;
};

double muon_sin_theta(float pt, float eta, float phi) {
    // Muon_v1::p4 uses TLorentzVector::SetPtEtaPhiM and TVector3::Theta.
    const double p = std::abs(static_cast<double>(pt));
    const double x = p * std::cos(static_cast<double>(phi));
    const double y = p * std::sin(static_cast<double>(phi));
    const double z = p * std::sinh(static_cast<double>(eta));
    const double theta = x == 0. && y == 0. && z == 0. ? 0. : std::atan2(std::sqrt(x*x + y*y), z);
    return std::sin(theta);
}

double track_eta(float q_over_p, float theta, float phi) {
    // TrackParticle_v1::eta builds a Cartesian GenVector from float components.
    float p = 10.e6;
    if (std::abs(q_over_p) > 0.) p = 1 / std::abs(q_over_p);
    const float sin_theta = std::sin(theta);
    const double x = p * sin_theta * std::cos(phi);
    const double y = p * sin_theta * std::sin(phi);
    const double z = p * std::cos(theta);
    const double rho = std::sqrt(x*x + y*y);
    if (rho > 0.) {
        const double u = z / rho;
        static const double taylor_at = std::pow(std::numeric_limits<double>::epsilon(), -.25);
        if (std::abs(u) < taylor_at) {
            return std::log(u + std::sqrt(u*u + 1.));
        }
        return z > 0. ? std::log(2.*u + .5/u) : -std::log(-2.*u);
    }
    constexpr double infinite_eta = 22756.;
    if (z == 0.) return 0.;
    return z > 0. ? z + infinite_eta : z - infinite_eta;
}

float track_charge(float q_over_p) {
    if (q_over_p > 0.F) return 1.F;
    if (q_over_p < 0.F) return -1.F;
    return 0.F;
}

struct TrackInputs {
    TrackInputs(StageInputs context, const char* name)
        : available(context.input_index(std::string(name) + "_link_valid", Type::integer)),
          q_over_p(context.input_index(std::string(name) + "_q_over_p", Type::real)),
          theta(context.input_index(std::string(name) + "_theta", Type::real)),
          variance(context.input_index(std::string(name) + "_var_q_over_p", Type::real)),
          diag_size(context.input_index(std::string(name) + "_cov_diag_size", Type::integer)) {}

    double q_variance(Row row) const {
        // definingParametersCovMatrix uses the identity diagonal if size != 5.
        return row.integer(diag_size) == 5 ? row.stored(variance) : 1.;
    }

    size_t available, q_over_p, theta, variance, diag_size;
};

struct PrimaryInputs {
    PrimaryInputs(StageInputs track, StageInputs track_or_quality)
        : role(track_or_quality.input_index("primary_role", Type::integer)),
          d0(track.input_index("primary_d0", Type::real)),
          phi(track.input_index("primary_phi", Type::real)),
          z0(track.input_index("primary_z0", Type::real)),
          vz(track.input_index("primary_vz", Type::real)),
          var_d0(track.input_index("primary_var_d0", Type::real)),
          diag_size(track.input_index("primary_cov_diag_size", Type::integer)) {}

    size_t role, d0, phi, z0, vz, var_d0, diag_size;
};

struct HitInputs {
    explicit HitInputs(StageInputs context)
        : precision(context.input_index("precision_layers", Type::integer)),
          precision_holes(context.input_index("precision_hole_layers", Type::integer)),
          good_precision(context.input_index("good_precision_layers", Type::integer)),
          inner_small(context.input_index("inner_small_hits", Type::integer)),
          inner_large(context.input_index("inner_large_hits", Type::integer)),
          middle_small(context.input_index("middle_small_hits", Type::integer)),
          middle_large(context.input_index("middle_large_hits", Type::integer)),
          outer_small(context.input_index("outer_small_hits", Type::integer)),
          outer_large(context.input_index("outer_large_hits", Type::integer)),
          ext_small(context.input_index("extended_small_hits", Type::integer)),
          ext_large(context.input_index("extended_large_hits", Type::integer)),
          ext_small_holes(context.input_index("extended_small_holes", Type::integer)),
          small_good_sectors(context.input_index("is_small_good_sectors", Type::integer)),
          stgc1(context.input_index("eta_layer1_stgc_hits", Type::integer)),
          stgc2(context.input_index("eta_layer2_stgc_hits", Type::integer)),
          mm(context.input_index("mm_hits", Type::integer)),
          out_of_bounds(context.input_index("combined_out_of_bounds_hits", Type::integer)) {}

    int precision_layers(Row row, double abs_eta) const {
        const int original = row.hit(precision);
        row.hit(precision_holes);
        row.hit(good_precision);
        row.hit(inner_small);
        row.hit(inner_large);
        const int middle_s = row.hit(middle_small);
        const int middle_l = row.hit(middle_large);
        const int outer_s = row.hit(outer_small);
        const int outer_l = row.hit(outer_large);
        const int ext_s = row.hit(ext_small);
        const int ext_l = row.hit(ext_large);
        row.hit(ext_small_holes);
        row.hit(small_good_sectors);
        if (abs_eta > 1.3) {
            const int stgc_hits = row.hit(stgc1) + row.hit(stgc2);
            const int mm_hits = row.hit(mm);
            return (middle_s > 2 || middle_l > 2) + (outer_s > 2 || outer_l > 2) +
                   (ext_s > 2 || ext_l > 2) + (stgc_hits > 3 || mm_hits > 3);
        }
        return original;
    }

    size_t precision, precision_holes, good_precision, inner_small, inner_large;
    size_t middle_small, middle_large, outer_small, outer_large, ext_small, ext_large;
    size_t ext_small_holes, small_good_sectors, stgc1, stgc2, mm, out_of_bounds;
};

class AfterCalibration final : public PluginNode {
public:
    AfterCalibration(const PluginConfig& config, const PluginContext& context)
        : stage_(configured_stage(config)),
          eta_bits_(slots(context, {Stage::all}).input_index("eta_bits", Type::integer)),
          pt_(context.input_index("pt", Type::real)),
          eta_(slots(context, {Stage::track, Stage::quality}).input_index("eta", Type::real)),
          phi_(slots(context, {Stage::track}).input_index("phi", Type::real)),
          id_pt_(slots(context, {Stage::quality}).input_index("id_pt", Type::real)),
          me_pt_(slots(context, {Stage::quality}).input_index("me_pt", Type::real)),
          calibrated_tracks_(slots(context, {Stage::quality}).input_index("calibrated_track_momenta_available", Type::integer)),
          muon_type_(slots(context, {Stage::quality}).input_index("muon_type", Type::integer)),
          author_(slots(context, {Stage::quality}).input_index("author", Type::integer)),
          all_authors_(slots(context, {Stage::quality}).input_index("all_authors", Type::integer)),
          primary_(slots(context, {Stage::track}), slots(context, {Stage::track, Stage::quality})),
          pv_z_(slots(context, {Stage::track}).input_index("pv_z", Type::real)),
          sigma_x_(slots(context, {Stage::track}).input_index("beam_sigma_x", Type::real)),
          sigma_y_(slots(context, {Stage::track}).input_index("beam_sigma_y", Type::real)),
          sigma_xy_(slots(context, {Stage::track}).input_index("beam_sigma_xy", Type::real)),
          pixel_hits_(slots(context, {Stage::quality}).input_index("pixel_hits", Type::integer)),
          pixel_dead_(slots(context, {Stage::quality}).input_index("pixel_dead", Type::integer)),
          sct_hits_(slots(context, {Stage::quality}).input_index("sct_hits", Type::integer)),
          sct_dead_(slots(context, {Stage::quality}).input_index("sct_dead", Type::integer)),
          pixel_holes_(slots(context, {Stage::quality}).input_index("pixel_holes", Type::integer)),
          sct_holes_(slots(context, {Stage::quality}).input_index("sct_holes", Type::integer)),
          id_(slots(context, {Stage::quality}), "id"), me_(slots(context, {Stage::quality}), "me"),
          cb_(slots(context, {Stage::quality}), "cb"),
          cb_phi_(slots(context, {Stage::quality}).input_index("cb_phi", Type::real)),
          hits_(slots(context, {Stage::quality})),
          calo_score_(slots(context, {Stage::quality}).input_index("calo_score", Type::real)),
          calo_score_available_(slots(context, {Stage::quality}).input_index("calo_score_available", Type::integer)),
          lowpt_parameters_(slots(context, {Stage::quality}).input_index("lowpt_parameters_mask", Type::integer)),
          track_iso_(slots(context, {Stage::isolation}).input_index("track_iso", Type::real)),
          neutral_iso_(slots(context, {Stage::isolation}).input_index("neutral_iso", Type::real)),
          isolation_available_(slots(context, {Stage::isolation}).input_index("isolation_available_mask", Type::integer)),
          min_pt_(static_cast<float>(config.real("min_pt_mev"))),
          max_d0_(static_cast<float>(config.real("max_d0_significance"))),
          max_dz_(static_cast<float>(config.real("max_delta_z0_sin_theta_mm"))),
          medium_max_eta_(config.real("medium_max_eta")),
          neutral_weight_(config.real("iso_neutral_weight")),
          iso_threshold_(config.real("iso_relative_cut")) {
        config.require_keys({"stage", "min_pt_mev", "max_d0_significance", "max_delta_z0_sin_theta_mm",
                             "medium_max_eta", "iso_neutral_weight", "iso_relative_cut"});
        require_shape(context, output_size());
    }

    double evaluate(const Values& values) const override {
        const Row row{values};
        const auto pt = row.stored(pt_);
        if (stage_ == Stage::pt) return pass_pt(pt) ? accepted : rejected;
        if (stage_ == Stage::track) return track_selection(row, pt);
        if (stage_ == Stage::isolation) return isolation_selection(row, pt);
        throw std::invalid_argument("Muon selection stage requires an array output");
    }

    size_t output_size() const override {
        if (stage_ == Stage::all) return 6;
        return stage_ == Stage::quality ? 2 : 0;
    }

    void evaluate_array(const Values& values, double* out, size_t size) const override {
        if (size != output_size()) {
            throw std::invalid_argument("Unexpected muon selection output shape");
        }
        const Row row{values};
        if (stage_ == Stage::quality) {
            out[0] = medium_selection(row, row.stored(pt_));
            out[1] = is_bad_muon(row);
            return;
        }
        if (stage_ != Stage::all) throw std::invalid_argument("Scalar selection stage");
        out[0] = rejected;
        out[1] = track_blank;
        out[2] = out[3] = out[4] = out[5] = 0.;
        const auto eta_bits = row.integer(eta_bits_);
        if (eta_bits != accepted) {
            if (eta_bits != rejected) throw std::invalid_argument("Expected release eta selection bits");
            return;
        }
        const auto pt = row.stored(pt_);
        if (!pass_pt(pt)) return;
        out[0] = accepted;
        const auto track_bits = track_selection(row, pt);
        out[1] = track_bits;
        if (track_bits != accepted) return;
        const bool medium = medium_selection(row, pt);
        out[2] = medium;
        out[3] = is_bad_muon(row);
        if (!medium) return;
        const bool isolated = isolation_selection(row, pt);
        out[4] = isolated;
        out[5] = isolated;
    }

private:
    StageInputs slots(const PluginContext& context, std::initializer_list<Stage> needed) const {
        if (stage_ == Stage::all) return {context, true};
        for (const auto stage : needed) {
            if (stage == stage_) return {context, true};
        }
        return {context, false};
    }

    bool pass_pt(float pt) const { return std::isfinite(pt) && pt >= min_pt_; }

    uint32_t track_selection(Row row, float pt) const {
        const auto role = row.integer(primary_.role);
        if (role == no_track) return track_blank;
        if (role < id_track || role > ms_track) {
            throw std::invalid_argument("Unexpected primary track role");
        }
        uint32_t result = track_blank | 1;
        if (pass_d0(row)) result |= 2;

        const float z0_plus_vz = row.stored(primary_.z0) + row.stored(primary_.vz);
        const double vertex_z = row.stored(pv_z_);
        const float dz_sin_theta = (z0_plus_vz - vertex_z) *
                                   muon_sin_theta(pt, row.stored(eta_), row.stored(phi_));
        if (std::abs(dz_sin_theta) < max_dz_) result |= 4;
        return result;
    }

    bool pass_d0(Row row) const {
        const double sx = row.stored(sigma_x_);
        const double sy = row.stored(sigma_y_);
        const double sxy = row.stored(sigma_xy_);
        if (!(sx*sx + sy*sy >= 2*std::abs(sxy))) return false;
        const double phi = row.stored(primary_.phi);
        const double s = std::sin(phi);
        const double c = std::cos(phi);
        const double beam_variance = s * (s * (sx*sx) - c*sxy) + c * (c * (sy*sy) - s*sxy);
        const auto diag_size = row.integer(primary_.diag_size);
        if (diag_size < 0) return false;
        if (diag_size == 0) throw std::out_of_range("Released d0 variance lookup found an empty vector");
        const double variance = row.stored(primary_.var_d0);
        if (variance <= 0.) return false;
        const float significance = row.stored(primary_.d0) / std::sqrt(variance + beam_variance);
        return std::abs(significance) < max_d0_;
    }

    bool id_hits_pass(Row row, int64_t muon_type, int64_t author, double abs_eta) const {
        if (author == muid_sa && abs_eta > 2.5) return true;
        if (muon_type == silicon_forward) return forward_cb(row, 2.5);
        if (!row.flag(id_.available) && row.integer(primary_.role) == no_track) return false;
        const int pixel_hits = row.hit(pixel_hits_);
        const int pixel_dead = row.hit(pixel_dead_);
        if (pixel_hits + pixel_dead == 0) return false;
        const int sct_hits = row.hit(sct_hits_);
        const int sct_dead = row.hit(sct_dead_);
        if (sct_hits + sct_dead <= 4) return false;
        const int pixel_holes = row.hit(pixel_holes_);
        const int sct_holes = row.hit(sct_holes_);
        return pixel_holes + sct_holes < 3;
    }

    bool forward_cb(Row row, double min_eta) const {
        return row.flag(cb_.available) &&
               std::abs(track_eta(row.stored(cb_.q_over_p), row.stored(cb_.theta), row.stored(cb_phi_))) > min_eta;
    }

    bool muon_preselection(Row row, float pt, int64_t type, int64_t author, double abs_eta) const {
        if (type == combined) return author != staco;
        if (type == calo_tagged && abs_eta < 0.105) return calo_quality(row, pt);
        if (type == segment_tagged && abs_eta < 0.105) return true;
        if (author == muid_sa && abs_eta > 2.4) return true;
        return type == silicon_forward && forward_cb(row, 2.4);
    }

    bool calo_quality(Row row, float pt) const {
        if (!row.flag(calo_score_available_)) {
            throw std::runtime_error("Required xAOD CaloMuonScore is unavailable");
        }
        const float score = row.stored(calo_score_);
        const float gev = pt * (1. / 1000.);
        if (gev > 20.) return score >= .77;
        return score >= (-1.80e-4 * std::pow(gev, 3) + 5.02e-3 * std::pow(gev, 2)
                         - 4.62e-2 * gev + 1.12);
    }

    float q_over_p_significance(Row row) const {
        if (!row.flag(calibrated_tracks_)) {
            throw std::runtime_error("Calibrated ID and ME pT decorations are required");
        }
        const float me_p = row.stored(me_pt_) / std::sin(row.stored(me_.theta));
        const float id_p = row.stored(id_pt_) / std::sin(row.stored(id_.theta));
        const float sigma = std::sqrt(id_.q_variance(row) + me_.q_variance(row));
        return std::abs((track_charge(row.stored(me_.q_over_p)) / me_p)
                        - (track_charge(row.stored(id_.q_over_p)) / id_p)) / sigma;
    }

    bool all_authors_has_mu_tag_imo(Row row) const {
        const auto authors = row.integer(all_authors_);
        if (authors < 0 || authors > std::numeric_limits<uint16_t>::max()) {
            throw std::runtime_error("Required xAOD allAuthors is unavailable or outside uint16");
        }
        return (authors & (1 << mu_tag_imo)) != 0;
    }

    bool medium_selection(Row row, float pt) const {
        const double abs_eta = std::abs(static_cast<double>(row.stored(eta_)));
        if (abs_eta >= medium_max_eta_) return false;
        const auto type = row.integer(muon_type_);
        const auto author = row.integer(author_);
        // The release calls both independent parts even if the ID cuts fail.
        const bool pass_id = id_hits_pass(row, type, author, abs_eta);
        const bool pass_muon = muon_preselection(row, pt, type, author, abs_eta);
        if (!pass_id || !pass_muon || type == segment_tagged) return false;
        if (type == calo_tagged && abs_eta < 0.1 && calo_quality(row, pt)) return false;

        const int layers = hits_.precision_layers(row, abs_eta);
        if (type == combined) {
            const int out_of_bounds = row.hit(hits_.out_of_bounds);
            bool medium = false;
            if (out_of_bounds == 0) {
                const bool medium_layers = layers > 1 ||
                    (layers == 1 && row.hit(hits_.precision_holes) < 2 && abs_eta < 0.1);
                const bool has_pair = row.flag(id_.available) && row.flag(me_.available) &&
                                      me_.q_variance(row) > 0.;
                if (has_pair) {
                    const float significance = q_over_p_significance(row);
                    medium = std::abs(significance) < 7.0 && medium_layers;
                }
                // A missing pair with enough hits already returned Loose. The
                // later Loose-only improvement can require allAuthors.
                if (!medium && (has_pair || !medium_layers) && pt * (1. / 1000.) < 7.
                    && abs_eta < 1.3 && layers > 0 && author == mu_girl) {
                    all_authors_has_mu_tag_imo(row);
                }
            }
            check_low_pt_auxiliaries(row, pt, abs_eta, author, layers, out_of_bounds, medium);
            return medium;
        }
        if (author == muid_sa) return abs_eta > 2.5 && layers > 2;
        return type == silicon_forward && row.flag(cb_.available) && row.flag(me_.available)
               && forward_cb(row, 2.5) && layers > 2;
    }

    void check_low_pt_auxiliaries(Row row, float pt, double abs_eta, int64_t author,
                                 int layers, int out_of_bounds, bool medium) const {
        // CP computes this auxiliary quality even for Medium. Its bool result is
        // unused, but the low-pT accessors can throw before the Medium result.
        if ((author != muid_co && author != mu_girl) || pt * (1. / 1000.) > 18.
            || (abs_eta > 1.55 && !medium) || out_of_bounds > 0) return;
        const int min_stations = abs_eta > 1.3 && abs_eta < 1.55 ? 2 : 1;
        if (layers < min_stations) return;
        if (author == mu_girl && !all_authors_has_mu_tag_imo(row)) return;
        if (row.integer(lowpt_parameters_) != 7) {
            throw std::runtime_error("Required xAOD low-pT diagnostic parameter is unavailable");
        }
    }

    bool is_bad_muon(Row row) const {
        if (row.integer(muon_type_) != combined) return false;
        const bool has_me = row.flag(me_.available);
        if (has_me && me_.q_variance(row) < 0.) return true;
        if (!row.flag(id_.available) || !has_me || !row.flag(cb_.available)) return true;
        const double id_relative = std::sqrt(id_.q_variance(row)) / row.stored(id_.q_over_p);
        const double me_relative = std::sqrt(me_.q_variance(row)) / row.stored(me_.q_over_p);
        const double cb_relative = std::sqrt(cb_.q_variance(row)) / row.stored(cb_.q_over_p);
        return std::abs(id_relative / cb_relative) < 0.8 || std::abs(me_relative / cb_relative) < 0.8;
    }

    bool isolation_selection(Row row, float pt) const {
        const auto available = row.integer(isolation_available_);
        if (available < 0 || available > 3) throw std::invalid_argument("Unexpected isolation availability mask");
        constexpr double missing = std::numeric_limits<float>::max();
        const double track_iso = available & 1 ? row.stored(track_iso_) : missing;
        const double neutral_iso = available & 2 ? row.stored(neutral_iso_) : missing;
        const float value = std::abs(track_iso) + neutral_weight_ * (neutral_iso > 0. ? neutral_iso : 0.);
        const float cut = iso_threshold_ * pt;
        return value <= cut;
    }

    Stage stage_;
    size_t eta_bits_, pt_, eta_, phi_, id_pt_, me_pt_, calibrated_tracks_, muon_type_, author_, all_authors_;
    PrimaryInputs primary_;
    size_t pv_z_, sigma_x_, sigma_y_, sigma_xy_;
    size_t pixel_hits_, pixel_dead_, sct_hits_, sct_dead_, pixel_holes_, sct_holes_;
    TrackInputs id_, me_, cb_;
    size_t cb_phi_;
    HitInputs hits_;
    size_t calo_score_, calo_score_available_, lowpt_parameters_, track_iso_, neutral_iso_, isolation_available_;
    float min_pt_, max_d0_, max_dz_;
    double medium_max_eta_, neutral_weight_, iso_threshold_;
};

template <typename NodeType>
std::shared_ptr<const PluginNode> make_node(const PluginConfig& config, const PluginContext& context) {
    return std::make_shared<NodeType>(config, context);
}
} // namespace

void register_selection(correction::PluginRegistry& registry) {
    static_assert(correction::plugin_api_version == 4);
    registry.register_node("atlas.muon_selection.eta", 1, make_node<EtaSelection>);
    registry.register_node("atlas.muon_selection.track_roles", 1, make_node<TrackRoles>);
    registry.register_node("atlas.muon_selection.after_calibration", 1, make_node<AfterCalibration>);
}
