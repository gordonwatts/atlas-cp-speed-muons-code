/*
 * SPDX-License-Identifier: LGPL-2.1-or-later
 * Behavior adapted from ROOT v6-40-02, commit d4c0003542952bec9c4edc86c419784a32875e9d.
 * ROOT TRandom and TAxis credit Rene Brun and Fons Rademakers.
 * The original MT19937 implementation credits Makoto Matsumoto and Takuji Nishimura.
 * ROOT TRandom3 credits its CLHEP adaptation to Peter Malzacher.
 * The ACR method is due to Wolfgang Hörmann and Gerhard Derflinger.
 * The ROOT source credits UNU.RAN (Wolfgang Hörmann and Josef Leydold, WU Wien).
 * ROOT GenVector credits the FNAL/LCG MathLib teams. See NOTICE.md.
 */
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <random>
#include <span>
#include <stdexcept>

namespace atlas::momentum::root_compat {

using GaussianDraws = std::array<double, 6>;

inline std::uint32_t muon_seed(double phi, double eta, std::uint64_t event_number) {
    // Source expression keeps its addition order and widens the input before the cast.
    const double value = 1 + std::abs(phi) * 1E6 + std::abs(eta) * 1E3 + event_number;
    if (!(value >= 1.0 && value < 0x1p32)) {
        throw std::domain_error("ROOT-free MuonCalib seed needs a finite value in [1, 2^32)");
    }
    return static_cast<std::uint32_t>(value);
}

template<class NextWord>
double uniform_from_words(NextWord& next) {
    std::uint32_t word;
    do {
        word = static_cast<std::uint32_t>(next());
    } while (word == 0);
    return static_cast<double>(word) * 0x1p-32;
}

class Uniforms {
public:
    explicit Uniforms(std::uint32_t seed) : engine_(nonzero(seed)) {}

    double operator()() { return uniform_from_words(engine_); }

private:
    static std::uint32_t nonzero(std::uint32_t seed) {
        if (seed == 0) {
            throw std::domain_error("ROOT-free MuonCalib cannot reproduce the ROOT seed-zero entropy path");
        }
        return seed;
    }

    std::mt19937 engine_;
};

// The constants and evaluation order reproduce TRandom::Gaus(0, 1).
// A callable uniform source makes branch consumption observable in the probe.
template<class Uniform>
double gaussian(Uniform& uniform) {
    constexpr double upper_region = 0.516058551;
    constexpr double lower_region = 0.107981933;
    constexpr double central_region = 0.483941449;
    constexpr double log_pi_over_two = 0.4515827053;
    const auto finish = [](double value) { return 0.0 + 1.0 * value; };

    const double pick = uniform();
    if (pick > upper_region) return finish(4.132731354 * pick - 3.132731354);
    if (pick < lower_region) {
        const double shifted = 18.52161694 * pick - 1;
        return finish(shifted > 0 ? 1 + shifted : -1 + shifted);
    }

    if (pick < central_region) {
        const double draw = uniform();
        const double centered = draw - 1 + draw;
        const double reflected = centered > 0 ? 2 - centered : -2 - centered;
        if ((1.448242853 - pick) * (1.46754004 + std::abs(reflected)) < 3.307147487) {
            return finish(reflected);
        }
        const double square = centered * centered;
        if ((pick + 1.036467755) * (3.631288474 + square) < 5.295844968) {
            return finish(centered);
        }
        if (0.591923442 - pick < std::exp(-(reflected * reflected + log_pi_over_two) / 2)) {
            return finish(reflected);
        }
        if (pick + 0.375959516 < std::exp(-(square + log_pi_over_two) / 2)) {
            return finish(centered);
        }
    }

    constexpr double tail_height = 0.187308492;
    for (;;) {
        double x = uniform();
        double y = tail_height * uniform();
        const bool positive = 0.4571828819 - 0.7270572718 * x - y > 0;
        if (!positive) {
            x = 1 - x;
            y = tail_height - y;
        }
        const double magnitude = 2 + y / x;
        const double candidate = positive ? magnitude : -magnitude;
        if ((y - 0.8853395638 + x) * (0.2770276848 + x) + 0.2452635696 < 0) {
            return finish(candidate);
        }
        if (y < x + 0.03895759111 && candidate * candidate < 4 * (0.5029324303 - std::log(x))) {
            return finish(candidate);
        }
    }
}

inline GaussianDraws gaussians(std::uint32_t seed) {
    Uniforms uniform(seed);
    GaussianDraws draws;
    for (double& value : draws) value = gaussian(uniform);
    return draws;
}

inline int axis_find_fix_bin(double x, int bins, double low, double high, std::span<const double> edges) {
    if (x < low) return 0;
    if (!(x < high)) return bins + 1;

    if (edges.empty()) {
        const double width = (high - low) / bins;
        const int index = static_cast<int>((x - low) / width);
        const bool too_high = x < low + width * index;
        const bool too_low = low + width * (index + 1) <= x;
        return 1 + index - too_high + too_low;
    }

    const auto next = std::lower_bound(edges.begin(), edges.end(), x);
    const int index = static_cast<int>(next - edges.begin());
    return next != edges.end() && *next == x ? index + 1 : index;
}

inline std::size_t th2_global_bin(int x, int y, int nx, int ny) {
    const int xbin = std::clamp(x, 0, nx + 1);
    const int ybin = std::clamp(y, 0, ny + 1);
    return static_cast<std::size_t>(xbin) + (static_cast<std::size_t>(nx) + 2) * ybin;
}

inline double cartesian_pt(double px, double py) {
    return std::sqrt(px * px + py * py);
}

inline double angular_momentum(double pt, double eta) {
    constexpr double eta_max = 22756.0;
    if (pt > 0) return pt * std::cosh(eta);
    if (eta > eta_max) return eta - eta_max;
    if (eta < -eta_max) return -eta - eta_max;
    return 0;
}

// Muon_v1 uses a fixed positive mass. ROOT's negative mass constructor
// restrictions are outside this helper's input contract.
inline double positive_mass_energy(double pt, double eta, double mass) {
    const double p = angular_momentum(pt, eta);
    const double e2 = p * p + mass * mass;
    return std::sqrt(e2 > 0 ? e2 : 0);
}

} // namespace atlas::momentum::root_compat
