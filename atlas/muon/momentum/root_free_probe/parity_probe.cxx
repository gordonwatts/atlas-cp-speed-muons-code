// Compare this numerical boundary in two executables with independent linkage.
#include "root_compat.h"

#ifdef ATLAS_R01_ROOT_ORACLE
#include <Math/Vector4D.h>
#include <TAxis.h>
#include <TH2.h>
#include <TRandom3.h>
#endif

#include <bit>
#include <charconv>
#include <fstream>
#include <iostream>
#include <limits>
#include <set>
#include <string>
#include <string_view>
#include <vector>

namespace compat = atlas::momentum::root_compat;
constexpr double muon_mass = 105.658367;

#ifdef ATLAS_R01_ROOT_ORACLE
using Angular = ROOT::Math::LorentzVector<ROOT::Math::PtEtaPhiM4D<double>>;
using Cartesian = ROOT::Math::LorentzVector<ROOT::Math::PxPyPzM4D<double>>;
#endif

std::string hex(double value) {
    if (std::isnan(value)) return "nan";
    char output[16];
    const auto raw = std::bit_cast<std::uint64_t>(value);
    const auto converted = std::to_chars(output, output + sizeof(output), raw, 16);
    return std::string(output + sizeof(output) - converted.ptr, '0') + std::string(output, converted.ptr);
}

double from_hex(const std::string& encoded) {
    std::uint64_t raw = 0;
    const auto parsed = std::from_chars(encoded.data(), encoded.data() + encoded.size(), raw, 16);
    if (encoded.size() != 16 || parsed.ec != std::errc{} || parsed.ptr != encoded.data() + encoded.size()) {
        throw std::invalid_argument("Expected a binary64 hexadecimal field");
    }
    return std::bit_cast<double>(raw);
}

void print_gaussian(std::uint32_t seed) {
    std::cout << "gaussians\t" << seed;
#ifdef ATLAS_R01_ROOT_ORACLE
    TRandom3 random;
    random.SetSeed(seed);
    for (int i = 0; i < 6; ++i) std::cout << '\t' << hex(random.Gaus(0., 1.));
#else
    for (double draw : compat::gaussians(seed)) std::cout << '\t' << hex(draw);
#endif
    std::cout << '\n';
}

void print_uniforms(std::uint32_t seed) {
#ifdef ATLAS_R01_ROOT_ORACLE
    TRandom3 random;
    random.SetSeed(seed);
    const auto draw = [&]() { return random.Rndm(); };
#else
    compat::Uniforms random(seed);
    const auto draw = [&]() { return random(); };
#endif
    for (int i = 0; i < 1300; ++i) {
        std::cout << "uniform\t" << seed << '\t' << i << '\t' << hex(draw()) << '\n';
    }
}

struct Tape {
    std::span<const double> values;
    std::size_t used = 0;

    double operator()() {
        if (used == values.size()) throw std::runtime_error("Scripted uniforms exhausted");
        return values[used++];
    }
};

void print_tape(std::string_view name, std::span<const double> values) {
    Tape tape{values};
#ifdef ATLAS_R01_ROOT_ORACLE
    class TapedRoot : public TRandom {
    public:
        explicit TapedRoot(Tape& tape) : TRandom(1), tape_(tape) {}
        double Rndm() override { return tape_(); }
    private:
        Tape& tape_;
    };
    TapedRoot root(tape);
    const double result = root.Gaus(0., 1.);
#else
    const double result = compat::gaussian(tape);
#endif
    std::cout << "tape\t" << name << '\t' << tape.used << '\t' << hex(result) << '\n';
}

void tapes() {
    const auto probe = [](std::string_view name, double start, std::span<const double> continuation) {
        std::vector<double> values{start};
        values.insert(values.end(), continuation.begin(), continuation.end());
        // A known accepting tail pair supplies recovery to all boundary tapes.
        values.push_back(0.005);
        values.push_back(0.005);
        print_tape(name, values);
    };
    print_tape("upper", std::array{0.8});
    print_tape("lower-minus", std::array{0.02});
    print_tape("lower-plus", std::array{0.08});
    print_tape("central-first-squeeze", std::array{0.11, 0.001});
    print_tape("central-exp-z", std::array{0.11, 0.002});
    print_tape("central-exp-r", std::array{0.11, 0.003});
    print_tape("central-second-squeeze", std::array{0.11, 0.004});
    probe("central-rejected-and-tail", 0.161, std::array{0.055});
    print_tape("tail-positive-squeeze", std::array{0.5, 0.005, 0.005});
    print_tape("tail-positive-log", std::array{0.5, 0.005, 0.06});
    print_tape("tail-reflected-squeeze", std::array{0.5, 0.395, 0.99});
    print_tape("tail-reflected-log", std::array{0.5, 0.395, 0.985});
    probe("tail-retry", 0.5, std::array{0.005, 0.08});
    for (double threshold : {0.107981933, 0.483941449, 0.516058551}) {
        for (double pick : {std::nextafter(threshold, 0.), threshold, std::nextafter(threshold, 1.)}) {
            probe(hex(threshold) + "-" + hex(pick), pick, std::array{0.005, 0.005});
        }
    }
}

struct AxisSpec {
    std::string label;
    int bins;
    double low;
    double high;
    std::vector<double> edges;
};

void axis(const AxisSpec& spec) {
#ifdef ATLAS_R01_ROOT_ORACLE
    TAxis root;
    if (spec.edges.empty()) root.Set(spec.bins, spec.low, spec.high);
    else root.Set(spec.bins, spec.edges.data());
    const auto bin = [&](double x) { return root.FindFixBin(x); };
#else
    const auto bin = [&](double x) {
        return compat::axis_find_fix_bin(x, spec.bins, spec.low, spec.high, spec.edges);
    };
#endif
    constexpr double inf = std::numeric_limits<double>::infinity();
    constexpr double epsilon = std::numeric_limits<double>::epsilon();
    const double lower = spec.low + epsilon;
    const double upper = spec.high - epsilon;
    const auto compare = [&](double x) {
        const double clamped = std::max(std::min(x, upper), lower);
        std::cout << "axis\t" << spec.label << '\t' << hex(x) << '\t'
                  << bin(x) << '\t' << hex(clamped) << '\t' << bin(clamped) << '\n';
    };
    for (double x : {-inf, inf, -0.0, 0.0, std::numeric_limits<double>::quiet_NaN()}) compare(x);
    for (int i = 0; i <= spec.bins; ++i) {
        const double edge = spec.edges.empty()
            ? (i == spec.bins ? spec.high : spec.low + (spec.high - spec.low) / spec.bins * i)
            : spec.edges[i];
        const auto rounded = static_cast<float>(edge);
        for (double x : {
            std::nextafter(edge, -inf), edge, std::nextafter(edge, inf),
            static_cast<double>(std::nextafter(rounded, -std::numeric_limits<float>::infinity())),
            static_cast<double>(rounded),
            static_cast<double>(std::nextafter(rounded, std::numeric_limits<float>::infinity())),
        }) compare(x);
    }
}

void cells() {
#ifdef ATLAS_R01_ROOT_ORACLE
    TH2D reference("r01_compat_cell", "", 3, -2.6, 2.6, 2, -3.2, 3.2);
    reference.SetDirectory(nullptr);
#endif
    for (int x = -1; x <= 5; ++x) {
        for (int y = -1; y <= 4; ++y) {
#ifdef ATLAS_R01_ROOT_ORACLE
            const auto index = reference.GetBin(x, y);
#else
            const auto index = compat::th2_global_bin(x, y, 3, 2);
#endif
            std::cout << "cell\t" << x << '\t' << y << '\t' << index << '\n';
        }
    }
}

void vectors() {
    constexpr double inf = std::numeric_limits<double>::infinity();
    constexpr double nan = std::numeric_limits<double>::quiet_NaN();
    constexpr double pi = 3.14159265358979323846;
    for (double pt : {0., -0., -1., 1.e-300, 12.5, 1.e150, inf, nan}) {
        for (double eta : {
            0., -0., 0.77, -0.77, 710., -710., 22756., -22756.,
            std::nextafter(22756., inf), std::nextafter(-22756., -inf),
            inf, -inf, nan,
        }) {
            for (double phi : {-pi, pi, std::nextafter(pi, inf), nan}) {
#ifdef ATLAS_R01_ROOT_ORACLE
                const Angular vector(pt, eta, phi, muon_mass);
                const double p = vector.P();
                const double energy = vector.E();
#else
                const double p = compat::angular_momentum(pt, eta);
                const double energy = compat::positive_mass_energy(pt, eta, muon_mass);
#endif
                const double inverse = p == 0 ? 1.e12 : 1. / (p * 1e3);
                std::cout << "angular\t" << hex(pt) << '\t' << hex(eta) << '\t' << hex(phi) << '\t'
                          << hex(p) << '\t' << hex(inverse) << '\t' << hex(energy) << '\n';
            }
        }
    }
    for (float qp : {0.f, -0.f, 0.000016f, -0.000023f, 1.f, 1.e-30f, static_cast<float>(inf),
                     -static_cast<float>(inf), static_cast<float>(nan)}) {
        for (float theta : {0.f, std::numeric_limits<float>::denorm_min(), 1.57079633f, 3.14159265f}) {
            for (float phi : {-3.14159265f, 0.f, 3.14159265f}) {
                const float p = std::fabs(qp) > 0. ? 1 / std::fabs(qp) : 10.e6f;
                const float sin_theta = std::sin(theta);
                const float px = p * sin_theta * std::cos(phi);
                const float py = p * sin_theta * std::sin(phi);
#ifdef ATLAS_R01_ROOT_ORACLE
                const float pz = p * std::cos(theta);
                const double pt = Cartesian{px, py, pz, muon_mass}.pt();
#else
                const double pt = compat::cartesian_pt(px, py);
#endif
                std::cout << "track\t" << hex(qp) << '\t' << hex(theta) << '\t' << hex(phi) << '\t' << hex(pt) << '\n';
            }
        }
    }
}

void seeds(const char* filename) {
    const std::set<std::uint32_t> special{1, 2, 4357, 0x8000, 0x10000000, 0x7fffffff, 0x80000000, 0xfffffffe, 0xffffffff};
    auto all = special;
    std::ifstream stream(filename);
    if (!stream) throw std::invalid_argument("Cannot open seed fixture");
    std::uint64_t seed;
    while (stream >> seed) {
        if (!seed || seed > std::numeric_limits<std::uint32_t>::max()) throw std::invalid_argument("Invalid positive seed fixture");
        all.insert(static_cast<std::uint32_t>(seed));
    }
    if (!stream.eof()) throw std::invalid_argument("Malformed seed fixture");
    for (auto word : all) print_gaussian(word);
    for (auto word : special) print_uniforms(word);

    for (const auto event : {0ULL, 87596109ULL, 4294967294ULL}) {
        const double eta = event > 4000000000ULL ? 0. : static_cast<float>(-0.419172);
        const double phi = event > 4000000000ULL ? 0. : static_cast<float>(1.410509);
#ifdef ATLAS_R01_ROOT_ORACLE
        const auto calculated = static_cast<std::uint32_t>(1 + std::abs(phi) * 1E6 + std::abs(eta) * 1E3 + event);
#else
        const auto calculated = compat::muon_seed(phi, eta, event);
#endif
        std::cout << "seed\t" << event << '\t' << hex(eta) << '\t' << hex(phi) << '\t' << calculated << '\n';
    }
#ifndef ATLAS_R01_ROOT_ORACLE
    const std::array<std::uint32_t, 4> words{0, 0, 1, 0xffffffff};
    std::size_t position = 0;
    auto next_word = [&]() { return words.at(position++); };
    if (compat::uniform_from_words(next_word) != 0x1p-32 || position != 3 ||
        compat::uniform_from_words(next_word) != 1.0 - 0x1p-32 || position != 4) {
        throw std::runtime_error("ROOT-compatible uniform words did not skip both zeros");
    }
    int rejected = 0;
    try { compat::gaussians(0); } catch (const std::domain_error&) { ++rejected; }
    try { compat::muon_seed(0., 0., 4294967295ULL); } catch (const std::domain_error&) { ++rejected; }
    try { compat::muon_seed(std::numeric_limits<double>::quiet_NaN(), 0., 1); } catch (const std::domain_error&) { ++rejected; }
    try { compat::muon_seed(0., std::numeric_limits<double>::infinity(), 1); } catch (const std::domain_error&) { ++rejected; }
    if (rejected != 4) throw std::runtime_error("Unsupported standalone seed domains were accepted");
#endif
}

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "Usage: parity_probe SEEDS AXES\n";
        return 2;
    }
    seeds(argv[1]);
    tapes();
    axis({"uniform-synthetic", 48, -2.6, 2.6, {}});
    axis({"uneven-synthetic", 4, -3.2, 3.2, {-3.2, -1.01, 0., 1.55, 3.2}});
    std::ifstream atoms(argv[2]);
    if (!atoms) throw std::invalid_argument("Cannot open axis fixture");
    std::string label, low, high, edge;
    int bins, n_edges;
    while (atoms >> label >> bins >> low >> high >> n_edges) {
        AxisSpec spec{label, bins, from_hex(low), from_hex(high), {}};
        if (bins < 1 || (n_edges != 0 && n_edges != bins + 1)) throw std::invalid_argument("Malformed axis fixture shape");
        for (int i = 0; i < n_edges; ++i) {
            if (!(atoms >> edge)) throw std::invalid_argument("Missing axis fixture edge");
            spec.edges.push_back(from_hex(edge));
        }
        axis(spec);
    }
    if (!atoms.eof()) throw std::invalid_argument("Malformed axis fixture");
    cells();
    vectors();
}
