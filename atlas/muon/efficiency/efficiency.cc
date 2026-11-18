#include "efficiency.h"
#include "correction_plugin.h"

#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace {
using correction::PluginConfig;
using correction::PluginContext;
using correction::PluginNode;
using correction::Variable;
using Values = std::vector<Variable::Type>;
using Node = std::shared_ptr<const PluginNode>;
using Type = Variable::VarType;

constexpr double skipped = -1;
constexpr double out_of_validity = 1;
constexpr double ok = 2;
constexpr double missing = std::numeric_limits<double>::quiet_NaN();

void require_shape(const PluginContext& context, size_t output_size, size_t children) {
    if (context.output_size() != output_size || context.child_count() != children) {
        throw std::invalid_argument("Unexpected efficiency node output shape or child count");
    }
}

Node require_child(const PluginContext& context, size_t index, size_t output_size) {
    auto node = context.child(index);
    if (node->output_size() != output_size) {
        throw std::invalid_argument("Unexpected efficiency child output shape");
    }
    return node;
}

class MissingBin final : public PluginNode {
public:
    MissingBin(const PluginConfig& config, const PluginContext& context) {
        config.require_keys({});
        require_shape(context, 0, 0);
    }

    double evaluate(const Values&) const override { return missing; }
};

class PtGeV final : public PluginNode {
public:
    PtGeV(const PluginConfig& config, const PluginContext& context)
        : pt_(context.input_index("pt", Type::real)) {
        config.require_keys({});
        require_shape(context, 0, 0);
    }

    double evaluate(const Values& values) const override {
        // PtAxisHandler assigns a double division to its float output.
        return static_cast<float>(std::get<double>(values[pt_]) / 1000.);
    }

private:
    size_t pt_;
};

class ToolRoute final : public PluginNode {
public:
    ToolRoute(const PluginConfig& config, const PluginContext& context)
        : run_(context.input_index("random_run_number", Type::integer)),
          pt_(context.input_index("pt", Type::real)),
          eta_(context.input_index("eta", Type::real)),
          phi_(context.input_index("phi", Type::real)),
          charge_(context.input_index("charge", Type::real)),
          type_(context.input_index("muon_type", Type::integer)),
          gate_(context.input_index("gate", Type::integer)),
          low_pt_(config.real("low_pt_mev")),
          forward_eta_(config.real("forward_eta")),
          calo_type_(config.integer("calo_type")),
          skip_sf_(static_cast<float>(config.real("skip_sf"))) {
        config.require_keys({"measurement", "low_pt_mev", "forward_eta", "calo_type", "skip_sf"});
        const auto measurement = config.string("measurement");
        if (measurement == "reco") {
            reco_ = true;
        } else if (measurement != "iso" && measurement != "ttva") {
            throw std::invalid_argument("Unknown efficiency measurement");
        }
        const size_t maps = reco_ ? 5 : 1;
        require_shape(context, 2, maps + 1);
        states_ = require_child(context, 0, 0);
        for (size_t i = 0; i < maps; ++i) {
            maps_.push_back(require_child(context, i + 1, 2));
        }
    }

    double evaluate(const Values&) const override {
        throw std::invalid_argument("Efficiency result has two components");
    }

    size_t output_size() const override { return 2; }

    void evaluate_array(const Values& values, double* output, size_t size) const override {
        if (size != output_size()) {
            throw std::invalid_argument("Efficiency result requires two output components");
        }
        // Category performs an exact membership check even for a skipped object.
        states_->evaluate(values);
        const auto gate = std::get<int64_t>(values[gate_]);
        if (gate == 0) {
            output[0] = skip_sf_;
            output[1] = skipped;
            return;
        }
        if (gate != 1) {
            throw std::invalid_argument("Efficiency gate must be 0 or 1");
        }

        Values inputs(values);
        const auto run = std::get<int64_t>(values[run_]);
        if (run < 0 || run > std::numeric_limits<uint32_t>::max()) {
            throw std::invalid_argument("RandomRunNumber must fit uint32");
        }
        inputs[run_] = run == 0 ? int64_t{999999} : run;
        for (auto index : {pt_, eta_, phi_, charge_}) {
            inputs[index] = static_cast<double>(static_cast<float>(std::get<double>(values[index])));
        }
        maps_[collection(inputs)]->evaluate_array(inputs, output, size);
    }

private:
    size_t collection(const Values& inputs) const {
        if (!reco_) {
            return 0;
        }
        const auto pt = std::get<double>(inputs[pt_]);
        const auto eta = std::abs(std::get<double>(inputs[eta_]));
        const bool calo = std::get<int64_t>(inputs[type_]) == calo_type_;
        // EffiCollection::FindContainer checks forward eta before CaloTagged
        // below the crossover. Above it CaloTagged precedes the eta check.
        if (pt < low_pt_) {
            return eta >= forward_eta_ ? 2 : (calo ? 4 : 3);
        }
        if (calo) {
            return 1;
        }
        return eta < forward_eta_ ? 0 : 2;
    }

    size_t run_, pt_, eta_, phi_, charge_, type_, gate_;
    double low_pt_, forward_eta_;
    int64_t calo_type_;
    float skip_sf_;
    bool reco_ = false;
    Node states_;
    std::vector<Node> maps_;
};

class MapResult final : public PluginNode {
public:
    MapResult(const PluginConfig& config, const PluginContext& context)
        : pt_(context.input_index("pt", Type::real)),
          eta_(context.input_index("eta", Type::real)),
          type_(context.input_index("muon_type", Type::integer)),
          ttva_(config.integer("is_ttva") == 1),
          fallback_(static_cast<float>(config.real("fallback"))),
          forward_sf_(static_cast<float>(config.real("forward_sf"))),
          sign_(static_cast<float>(config.integer("sign"))) {
        config.require_keys({"is_ttva", "fallback", "forward_sf", "sign", "kinematic"});
        const auto mode = config.string("kinematic");
        if (mode == "none") {
            kine_ = Kinematic::none;
        } else if (mode == "closure") {
            kine_ = Kinematic::closure;
        } else if (mode == "primordial") {
            kine_ = Kinematic::primordial;
        } else if (mode == "flatness") {
            kine_ = Kinematic::flatness;
        } else {
            throw std::invalid_argument("Unknown efficiency kinematic handler");
        }
        if (sign_ != -1.F && sign_ != 0.F && sign_ != 1.F) {
            throw std::invalid_argument("Invalid efficiency systematic sign");
        }
        const size_t children = kine_ == Kinematic::none ? 1 : kine_ == Kinematic::flatness ? 4 : 2;
        require_shape(context, 2, children);
        for (size_t i = 0; i < children; ++i) {
            maps_.push_back(require_child(context, i, 0));
        }
    }

    double evaluate(const Values&) const override {
        throw std::invalid_argument("Efficiency result has two components");
    }

    size_t output_size() const override { return 2; }

    void evaluate_array(const Values& values, double* output, size_t size) const override {
        if (size != output_size()) {
            throw std::invalid_argument("Efficiency result requires two output components");
        }
        const double abs_eta = std::abs(std::get<double>(values[eta_]));
        if (ttva_ && abs_eta > 2.5 && abs_eta <= 2.7 &&
            std::get<int64_t>(values[type_]) == 1) {
            // MuonStandAlone is 1 in the release's xAOD MuonType.
            output[0] = forward_sf_;
            output[1] = ok;
            return;
        }
        const double raw = maps_[0]->evaluate(values);
        if (std::isnan(raw)) {
            output[0] = fallback_;
            output[1] = out_of_validity;
            return;
        }
        float sf = static_cast<float>(raw);
        output[1] = apply_kinematic(values, sf);
        output[0] = sf;
    }

private:
    enum class Kinematic { none, closure, primordial, flatness };

    double apply_kinematic(const Values& values, float& sf) const {
        if (kine_ == Kinematic::none) {
            return ok;
        }
        const auto pt = static_cast<float>(std::get<double>(values[pt_]));
        if (kine_ == Kinematic::closure) {
            const double closure = maps_[1]->evaluate(values);
            if (std::isnan(closure)) {
                return out_of_validity;
            }
            sf *= 1. + sign_ * std::abs(closure);
            return ok;
        }
        if (kine_ == Kinematic::primordial) {
            if (pt <= 200.e3) {
                return ok;
            }
            const double loss = maps_[1]->evaluate(values);
            if (std::isnan(loss)) {
                return out_of_validity;
            }
            sf *= 1. + sign_ * std::abs(loss) * pt / 1.0e6;
            return ok;
        }

        const double flatness = maps_[1]->evaluate(values);
        const double loss = pt > 200.e3 ? maps_[3]->evaluate(values) : missing;
        const bool valid_loss = !std::isnan(loss);
        if (std::isnan(flatness) && !valid_loss) {
            return out_of_validity;
        }
        float systematic = static_cast<float>(flatness);
        if (valid_loss) {
            const float loss_syst = std::abs(loss * pt / 1.0e6);
            const float error = std::isnan(flatness) ? 0.F : std::abs(maps_[2]->evaluate(values));
            if (std::isnan(flatness) || loss_syst < error || error == 0.F || pt > 500.e3) {
                systematic = loss_syst;
            }
        }
        // In this handler 1 + sign * abs(systematic) is float arithmetic.
        sf *= 1 + sign_ * std::abs(systematic);
        return ok;
    }

    size_t pt_, eta_, type_;
    bool ttva_;
    float fallback_, forward_sf_, sign_;
    Kinematic kine_ = Kinematic::none;
    std::vector<Node> maps_;
};

class FloatProduct final : public PluginNode {
public:
    FloatProduct(const PluginConfig& config, const PluginContext& context)
        : factors_{context.input_index("reco_sf", Type::real),
                   context.input_index("iso_sf", Type::real),
                   context.input_index("ttva_sf", Type::real)} {
        config.require_keys({});
        require_shape(context, 0, 0);
    }

    double evaluate(const Values& values) const override {
        float scale_factor = 1.F;
        for (auto index : factors_) {
            scale_factor *= static_cast<float>(std::get<double>(values[index]));
        }
        return scale_factor;
    }

private:
    std::array<size_t, 3> factors_;
};

template <typename NodeType>
Node make_node(const PluginConfig& config, const PluginContext& context) {
    return std::make_shared<NodeType>(config, context);
}
} // namespace

void register_efficiency(correction::PluginRegistry& registry) {
    static_assert(correction::plugin_api_version == 4);
    registry.register_node("atlas.muon_eff.missing_bin", 1, make_node<MissingBin>);
    registry.register_node("atlas.muon_eff.pt_gev", 1, make_node<PtGeV>);
    registry.register_node("atlas.muon_eff.tool", 1, make_node<ToolRoute>);
    registry.register_node("atlas.muon_eff.map_result", 1, make_node<MapResult>);
    registry.register_node("atlas.muon_eff.float_product", 1, make_node<FloatProduct>);
}
