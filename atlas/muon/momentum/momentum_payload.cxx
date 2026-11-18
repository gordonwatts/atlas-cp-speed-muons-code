#include "momentum_payload.h"
#include "root_compat.h"

#include "correction_plugin.h"

#include <rapidjson/document.h>

#include <algorithm>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string_view>

namespace atlas::momentum {
namespace {

using Json = rapidjson::Value;
using namespace std::literals;

void check(bool valid, const char* message) {
    if (!valid) throw std::invalid_argument(message);
}

const Json& member(const Json& object, const char* key) {
    if (!object.IsObject() || !object.HasMember(key)) {
        throw std::invalid_argument(std::string("Missing momentum configuration field: ") + key);
    }
    return object[key];
}

void check_size(const Json& value, std::size_t size) {
    check(value.IsArray() && value.Size() == size, "Wrong momentum configuration array size");
}

std::string_view string_value(const Json& value) {
    check(value.IsString(), "Expected a momentum configuration string");
    return {value.GetString(), value.GetStringLength()};
}

double binary64(const Json& value) {
    static_assert(std::numeric_limits<double>::is_iec559 && sizeof(double) == 8);
    const auto encoded = string_value(value);
    check(encoded.size() == 16, "Expected a sixteen digit binary64 encoding");
    std::uint64_t bits = 0;
    const auto result = std::from_chars(encoded.begin(), encoded.end(), bits, 16);
    check(result.ec == std::errc{} && result.ptr == encoded.end(), "Invalid binary64 digits");
    return std::bit_cast<double>(bits);
}

Axis axis_from_json(const Json& value) {
    check_size(value, 4);
    check(value[0].IsInt() && value[0].GetInt() > 0, "Invalid momentum calibration axis size");
    const int count = value[0].GetInt();
    const double low = binary64(value[1]);
    const double high = binary64(value[2]);
    check(std::isfinite(low) && std::isfinite(high) && low < high, "Invalid momentum calibration axis bounds");
    check(value[3].IsArray(), "Invalid momentum calibration axis edges");
    Axis result;
    result.bins = count;
    result.minimum = low;
    result.maximum = high;
    if (!value[3].Empty()) {
        check_size(value[3], static_cast<std::size_t>(count) + 1);
        for (const auto& entry : value[3].GetArray()) {
            const double edge = binary64(entry);
            check(std::isfinite(edge) && (result.edges.empty() || result.edges.back() < edge),
                  "Invalid momentum calibration axis edge");
            result.edges.push_back(edge);
        }
        check(result.edges.front() == low && result.edges.back() == high, "Inconsistent axis bounds");
    }
    constexpr auto epsilon = std::numeric_limits<double>::epsilon();
    result.lower = low + epsilon;
    result.upper = high - epsilon;
    return result;
}

Histogram histogram_from_json(const Json& value) {
    check_size(value, 3);
    Histogram result{axis_from_json(value[0]), axis_from_json(value[1]), {}};
    const auto nx = static_cast<std::size_t>(result.eta.bins) + 2;
    const auto ny = static_cast<std::size_t>(result.phi.bins) + 2;
    check_size(value[2], nx * ny);
    result.content.reserve(nx * ny);
    for (const auto& cell : value[2].GetArray()) result.content.push_back(binary64(cell));
    return result;
}

constexpr std::array year_names{"Data16"sv, "Data17"sv, "Data18"sv, "Data22"sv,
                                "Data23"sv, "Data24"sv, "Data25"sv, "Run4"sv};

void read_period(PeriodMaps& period, std::string_view year, const Json& value) {
    check(string_value(member(value, "name")) == year, "Unexpected year in momentum maps");
    const auto& expected = member(value, "expected");
    check_size(expected, expected_map_count);
    for (rapidjson::SizeType i = 0; i < expected.Size(); ++i) {
        period.expected[i] = histogram_from_json(expected[i]);
    }
    const auto& scale = member(value, "scale");
    const auto& sagitta = member(value, "sagitta");
    check_size(scale, detector_count);
    check_size(sagitta, detector_count);
    for (rapidjson::SizeType detector = 0; detector < detector_count; ++detector) {
        check_size(scale[detector], scale_parameter_count * scale_variant_count);
        for (rapidjson::SizeType i = 0; i < scale[detector].Size(); ++i) {
            period.scale[detector][i] = histogram_from_json(scale[detector][i]);
        }
        check_size(sagitta[detector], sagitta_map_count);
        for (rapidjson::SizeType i = 0; i < sagitta[detector].Size(); ++i) {
            if (i == data_pt_extra && sagitta[detector][i].IsNull()) continue;
            period.sagitta[detector][i] = histogram_from_json(sagitta[detector][i]);
        }
    }
}

constexpr std::pair<std::string_view, Action> actions[] = {
    {"nominal", Action::nominal}, {"smear_cb", Action::smear_cb}, {"smear_id", Action::smear_id},
    {"smear_me", Action::smear_me}, {"scale_cb_s1", Action::scale_cb_s1}, {"scale_cb_s0", Action::scale_cb_s0},
    {"scale_id", Action::scale_id}, {"scale_me_s1", Action::scale_me_s1}, {"scale_me_s0", Action::scale_me_s0},
    {"datastat", Action::datastat}, {"global", Action::global}, {"ptextra", Action::ptextra},
    {"negeta", Action::negeta}, {"poseta", Action::poseta}, {"rho", Action::rho}
};

State read_state(const Json& value) {
    check_size(value, 2);
    const auto name = string_value(value[0]);
    const auto action = std::find_if(std::begin(actions), std::end(actions),
                                     [&](const auto& item) { return name == item.first; });
    check(action != std::end(actions), "Unknown momentum systematic action");
    check(value[1].IsInt(), "Invalid momentum systematic direction type");
    const int direction = value[1].GetInt();
    check(action->second == Action::nominal ? direction == 0 : direction == 1 || direction == -1,
          "Invalid momentum systematic direction");
    return {action->second, direction};
}

} // namespace

// CalibContainer uses these precise epsilon operations and ROOT's FindFixBin.
int Axis::coordinate_bin(double coordinate) const {
    const double bounded = std::max(std::min(coordinate, upper), lower);
    return root_compat::axis_find_fix_bin(bounded, bins, minimum, maximum, edges);
}

double Histogram::lookup(double track_eta, double track_phi) const {
    const int x = eta.coordinate_bin(track_eta);
    const int y = phi.coordinate_bin(track_phi);
    return content.at(root_compat::th2_global_bin(x, y, eta.bins, phi.bins));
}

double PeriodMaps::scale_value(Detector detector, ScaleParameter parameter, ScaleVariant variant,
                              double eta, double phi) const {
    return scale[detector][parameter * scale_variant_count + variant].lookup(eta, phi);
}

Payload::Payload(const correction::PluginConfig& config) {
    config.require_keys({"release", "athena", "rng", "fixed_rho", "extra_rebias", "years", "states"});
    const auto encoded = config.json();
    rapidjson::Document doc;
    doc.Parse(encoded.data(), encoded.size());
    check(!doc.HasParseError(), "Invalid momentum calibration configuration JSON");
    check(string_value(member(doc, "release")) == "Recs2025_12_11_Run2Run3", "Wrong momentum recommendations");
    check(string_value(member(doc, "athena")) == "6ce3f7ae7c41dd9eaec170c3c349df15faef7e5c",
          "Wrong Athena source pin for momentum");
    check(string_value(member(doc, "rng")) == "ROOT-6.40.02-TRandom3-Gaus", "Wrong momentum RNG contract");
    fixed_rho = binary64(member(doc, "fixed_rho"));
    extra_rebias = binary64(member(doc, "extra_rebias"));
    check(fixed_rho == 1 && extra_rebias == static_cast<double>(0.00002f),
          "Wrong sagitta defaults for the frozen momentum prescription");

    const auto& years = member(doc, "years");
    check_size(years, period_count);
    for (rapidjson::SizeType i = 0; i < years.Size(); ++i) read_period(periods[i], year_names[i], years[i]);
    const auto& systematics = member(doc, "states");
    check(systematics.IsObject() && systematics.MemberCount() == 29, "Wrong momentum systematic count");
    std::array<std::array<int, 3>, std::size(actions)> counts{};
    for (const auto& item : systematics.GetObject()) {
        const auto name = string_value(item.name);
        const State state = read_state(item.value);
        auto [iterator, inserted] = states.emplace(name, state);
        check(inserted, "Duplicate momentum systematic");
        ++counts[static_cast<std::size_t>(state.action)][state.direction + 1];
    }
    check(states.contains("NOSYS") && states.at("NOSYS").action == Action::nominal,
          "Missing nominal momentum systematic");
    for (std::size_t i = 0; i < counts.size(); ++i) {
        check(counts[i] == (i == 0 ? std::array{0, 1, 0} : std::array{1, 0, 1}),
              "Missing or duplicate momentum systematic action");
    }
}

} // namespace atlas::momentum
