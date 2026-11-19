#include "correction.h"
#include "correction_plugin.h"

#include <array>
#include <bit>
#include <cerrno>
#include <cfenv>
#include <cstdint>
#include <dlfcn.h>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <typeinfo>
#include <vector>

namespace {
using Value = correction::Variable::Type;

std::vector<Value> make_row(const correction::Correction& node) {
    std::vector<Value> row;
    for (const auto& input : node.inputs()) {
        const auto name = input.name();
        using Type = correction::Variable::VarType;
        if (input.type() == Type::string) row.emplace_back(std::string("NOSYS"));
        else if (name == "called" || name == "is_simulation" || name.ends_with("_valid")) row.emplace_back(std::int64_t{1});
        else if (name == "run_number") row.emplace_back(std::int64_t{410000});
        else if (name == "event_number_lo") row.emplace_back(std::int64_t{87596109});
        else if (name.ends_with("_cov_diag_size")) row.emplace_back(std::int64_t{5});
        else if (name.ends_with("_cov_off_diag_size")) row.emplace_back(std::int64_t{6});
        else if (input.type() == Type::integer) row.emplace_back(std::int64_t{0});
        else if (name == "mu_pt") row.emplace_back(47876.25);
        else if (name == "mu_charge") row.emplace_back(1.0);
        else if (name == "mu_eta") row.emplace_back(0.75);
        else if (name.ends_with("_phi")) row.emplace_back(0.125);
        else if (name.ends_with("_theta")) row.emplace_back(1.35);
        else if (name.ends_with("_q_over_p")) row.emplace_back(0.00002);
        else if (name.ends_with("_cov_diag_4")) row.emplace_back(1.e-12);
        else if (name.find("_cov_diag_") != std::string::npos) row.emplace_back(1.0);
        else row.emplace_back(0.0);
    }
    return row;
}

void set(const correction::Correction& node, std::vector<Value>& row, const std::string& name, Value value) {
    const auto& inputs = node.inputs();
    for (std::size_t i = 0; i < inputs.size(); ++i) {
        if (inputs[i].name() == name) {
            row[i] = std::move(value);
            return;
        }
    }
    throw std::invalid_argument("incomplete momentum fixture: " + name);
}

using Reset = void (*)();
using Read = void (*)(std::uint64_t*);

void probe(const correction::Correction& node, Reset reset, Read read) {
    const auto nominal = make_row(node);
    const auto run = [&](const char* label, const std::vector<Value>& row, int rounding = FE_TONEAREST,
                         int prior = 0, int trap = 0) {
        std::array<double, 40> outputs{};
        std::array<std::uint64_t, 5> counts{};
        std::fenv_t saved;
        if (reset) reset();
        if (std::fegetenv(&saved) || std::fesetenv(FE_DFL_ENV) || std::fesetround(rounding) ||
            std::feraiseexcept(prior) || (trap && ::feenableexcept(trap) == -1)) {
            throw std::runtime_error("could not configure FENV fixture");
        }
        errno = EDOM;
        std::string error;
        int raised, code;
        try {
            node.evaluate_array(row, outputs.data(), node.output_size());
            code = errno;
            raised = std::fetestexcept(FE_ALL_EXCEPT);
        } catch (const std::exception& exc) {
            code = errno;
            raised = std::fetestexcept(FE_ALL_EXCEPT);
            error = std::string(typeid(exc).name()) + ":" + exc.what();
        }
        const int rounding_after = std::fegetround();
        const int traps_after = ::fegetexcept();
        if (std::fesetenv(&saved)) throw std::runtime_error("could not restore FENV fixture");
        std::cout << node.output_size() << '\t' << label << '\t' << raised << '\t' << code << '\t'
                  << rounding_after << '\t' << traps_after << '\t' << error;
        if (error.empty()) {
            for (std::size_t i = 0; i < node.output_size(); ++i) {
                std::cout << '\t' << std::hex << std::setw(16) << std::setfill('0')
                          << std::bit_cast<std::uint64_t>(outputs[i]) << std::dec;
            }
        }
        std::cout << '\n';
        if (read) {
            read(counts.data());
            std::cerr << node.output_size() << '\t' << label;
            for (const auto count : counts) std::cerr << '\t' << count;
            std::cerr << '\n';
        }
    };

    run("nominal", nominal);
    run("sticky", nominal, FE_TONEAREST, FE_INVALID | FE_UNDERFLOW | FE_INEXACT);
    run("downward", nominal, FE_DOWNWARD);
    run("upward", nominal, FE_UPWARD);
    run("overflow_trap", nominal, FE_TONEAREST, 0, FE_OVERFLOW);

    const auto changed = [&](const char* name, Value value) {
        auto row = nominal;
        set(node, row, name, std::move(value));
        return row;
    };
    run("rho_down", changed("systematic", std::string("MUON_SAGITTA_RHO__1down")));
    run("rho_up", changed("systematic", std::string("MUON_SAGITTA_RHO__1up")));
    run("data", changed("is_simulation", std::int64_t{0}));
    run("no_id", changed("id_valid", std::int64_t{0}));
    run("zero_me_pt", changed("me_theta", 0.0));
    run("tiny_id_pt", changed("id_q_over_p", 1.e34));
    run("nan_id_phi", changed("id_phi", std::numeric_limits<double>::quiet_NaN()));
    run("snan_id_phi", changed("id_phi", std::numeric_limits<double>::signaling_NaN()));
    run("nan_mu_eta", changed("mu_eta", std::numeric_limits<double>::quiet_NaN()));
    run("eta_flow", changed("mu_eta", 4.01));
    run("tiny_eta", changed("mu_eta", 0x1p-110));
    run("zero_eta", changed("mu_eta", -0.0));
    run("skipped_call", changed("called", std::int64_t{0}));
    run("unknown_state", changed("systematic", std::string("M13_UNKNOWN_STATE")));
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 4 && argc != 5) throw std::invalid_argument("provider production.json diagnostic.json [counts]");
    correction::PluginRegistry registry;
    registry.load_library(argv[1]);
    Reset reset = nullptr;
    Read read = nullptr;
    if (argc == 5) {
        auto* provider = dlopen(argv[1], RTLD_NOLOAD | RTLD_NOW);
        if (!provider) throw std::runtime_error(dlerror());
        reset = reinterpret_cast<Reset>(dlsym(provider, "atlas_muon_m13_reset_resolution_counts"));
        read = reinterpret_cast<Read>(dlsym(provider, "atlas_muon_m13_read_resolution_counts"));
        if (!reset || !read) throw std::runtime_error("missing M13 counters");
        std::cerr << "width\tfixture\tomitted_rows_12\toriginal_rows_12\toriginal_rows_40\tomitted_map_reads\toriginal_map_reads\n";
    }
    for (int i = 2; i < 4; ++i) {
        auto payload = correction::CorrectionSet::from_file(argv[i], registry);
        const auto node = payload->at("muon_momentum");
        if (node->output_size() != (i == 2 ? 12 : 40)) throw std::invalid_argument("unexpected fixture payload width");
        probe(*node, reset, read);
    }
}
