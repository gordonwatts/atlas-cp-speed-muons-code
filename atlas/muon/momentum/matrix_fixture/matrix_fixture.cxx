#include M11_SOURCE

#include <bit>
#include <iostream>

namespace {

using namespace atlas::momentum;

void require(bool condition, const char* what) {
    if (!condition) throw std::runtime_error(what);
}

void bits(double value) {
    std::cout << '\t' << std::hex << std::bit_cast<std::uint64_t>(value) << std::dec;
}

void combine(const char* name, const Covariance& id_cov, const Covariance& me_cov,
             double phi, int charge, Status expected, bool check_wrap = false) {
    Parameters id, me, cb;
    id << 0.125, -0.5, phi, 1.25, -0.25;
    me << 0.375, 0.5, phi, 1.25, 0.5;
    Covariance cb_cov;
#ifdef M11_CANDIDATE
    id[4] = std::abs(id[4]);
    me[4] = std::abs(me[4]);
    StatCombinationWeights weights;
    const Status status = prepare_stat_combination(id_cov, me_cov, weights);
    if (status == ok) {
        combine_stat_parameters(id, me, charge, weights, cb);
        cb_cov = weights.combined;
    }
#else
    const Status status = apply_stat_combination(id, id_cov, me, me_cov, charge, cb, cb_cov);
#endif
    require(status == expected, name);
    std::cout << name << '\t' << status;
    if (status == ok) {
        if (check_wrap) {
            const auto wrapped = phi - std::copysign(2. * std::numbers::pi, phi);
            require(cb[2] == wrapped && cb[4] == 0.375 * charge, name);
        }
        for (Eigen::Index index = 0; index < cb.size(); ++index) bits(cb[index]);
        for (Eigen::Index col = 0; col < cb_cov.cols(); ++col)
            for (Eigen::Index row = 0; row < cb_cov.rows(); ++row) bits(cb_cov(row, col));
    }
    std::cout << '\n';
}

}  // namespace

int main() {
    const Covariance unit = Covariance::Identity();
    Covariance singular = unit;
    singular(4, 4) = 0.;
    Covariance near_singular = unit;
    near_singular(4, 4) = 0x1p-20;
    Covariance below_rank_cut = unit;
    below_rank_cut(4, 4) = 0x1p-60;
    Covariance cancel = unit;
    cancel(0, 0) = -1.;
    Covariance sum_id = 0x1p40 * unit;
    Covariance sum_me = sum_id;
    sum_id(0, 0) = 1.;
    sum_me(0, 0) = -1. + 0x1p-23;
    Covariance correlated = unit;
    correlated(2, 4) = correlated(4, 2) = 0.25;
    correlated(1, 3) = correlated(3, 1) = -0.5;

    combine("wrap_positive_charge_negative", unit, unit, 3.5, -1, ok, true);
    combine("wrap_negative_charge_positive", unit, unit, -3.5, 1, ok, true);
    combine("wrap_and_zero_charge", unit, unit, 3.5, 0, ok, true);
    combine("singular_id", singular, unit, 0.1, 1, error);
    combine("singular_me", unit, singular, 0.1, 1, error);
    combine("singular_summed_weights", unit, cancel, 0.1, 1, error);
    const Eigen::FullPivLU<Covariance> sum_id_lu(sum_id), sum_me_lu(sum_me);
    const Covariance sum_id_weight = sum_id_lu.inverse(), sum_me_weight = sum_me_lu.inverse();
    require(sum_id_lu.isInvertible() && sum_me_lu.isInvertible() &&
            Eigen::FullPivLU<Covariance>(sum_id_weight + sum_me_weight).isInvertible() &&
            !Eigen::FullPivLU<Covariance>(sum_id + sum_me).isInvertible(), "summed covariance fixture");
    combine("rank_cut_only_at_summed_covariance", sum_id, sum_me, 0.1, 1, error);
    combine("rank_below_cut", below_rank_cut, unit, 0.1, 1, error);
    combine("near_singular", near_singular, unit, 0.1, 1, ok);
    combine("correlated", correlated, unit, -3.125, -1, ok);

    Muon muon;
    muon.tracks[CB].pt = 47.5;
    muon.tracks[ID].pt = 48.;
    muon.tracks[ME].pt = 46.;
    muon.tracks[ID].covariance = unit;
    muon.tracks[ME].covariance = singular;
    for (const auto corrected_id_pt : {0., 49.}) {
        double status = 9.;
        const double pt = stat_combination(muon, corrected_id_pt, 45., status);
        require(pt == 47.5 && status == (corrected_id_pt == 0. ? skipped : error), "pT fallback");
        std::cout << (corrected_id_pt == 0. ? "skip_before_singular_covariance" : "singular_pt_fallback");
        bits(status);
        bits(pt);
        std::cout << '\n';
    }
    muon.tracks[ME].covariance = unit;
    muon.tracks[CB].charge = -1;
    muon.tracks[ID].parameters[3] = muon.tracks[ME].parameters[3] = 1.25;
    double status = 9.;
    const double shifted_pt = stat_combination(muon, 49., 45., status);
    const double expected_pt = 47.5 * ((1. / 48. + 1. / 46.) / (1. / 49. + 1. / 45.));
    require(status == ok && shifted_pt != 47.5 && std::abs(shifted_pt - expected_pt) < 1e-12,
            "statistical ratio from both parameter vectors");
    std::cout << "distinct_nominal_and_shifted_pT";
    bits(status);
    bits(shifted_pt);
    std::cout << '\n';
}
