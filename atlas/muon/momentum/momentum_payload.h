#pragma once

#include <array>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace correction {
class PluginConfig;
}

namespace atlas::momentum {

enum Detector { CB, ID, ME, detector_count };
enum Period { data16, data17, data18, data22, data23, data24, data25, run4, period_count };
enum ScaleParameter { s0, s1, r0, r1, r2, scale_parameter_count };
enum ScaleVariant { nominal, up_error, down_error, scale_variant_count };
enum ExpectedMap { id_r1, id_r2, id_r2_tan2, me_r0, me_r1, me_r2, expected_map_count };
enum SagittaMap { data_nominal, data_stat, data_residual, data_pt_extra, mc_nominal, sagitta_map_count };

enum class Action {
    nominal, smear_cb, smear_id, smear_me, scale_cb_s1, scale_cb_s0,
    scale_id, scale_me_s1, scale_me_s0, datastat, global, ptextra, negeta, poseta, rho
};

struct State {
    Action action;
    int direction;
};

struct Axis {
    int bins = 0;
    double minimum = 0;
    double maximum = 0;
    std::vector<double> edges;
    double lower = 0;
    double upper = 0;

    int coordinate_bin(double coordinate) const;
};

struct Histogram {
    Axis eta;
    Axis phi;
    std::vector<double> content;

    double lookup(double track_eta, double track_phi) const;
};

struct PeriodMaps {
    std::array<std::array<Histogram, scale_parameter_count * scale_variant_count>, detector_count> scale;
    std::array<Histogram, expected_map_count> expected;
    std::array<std::array<std::optional<Histogram>, sagitta_map_count>, detector_count> sagitta;

    double scale_value(Detector detector, ScaleParameter parameter, ScaleVariant variant,
                       double eta, double phi) const;
};

struct Payload {
    std::array<PeriodMaps, period_count> periods;
    std::map<std::string, State, std::less<>> states;
    double fixed_rho = 0;
    double extra_rebias = 0;

    explicit Payload(const correction::PluginConfig& config);
};

} // namespace atlas::momentum
