#include "framework_runtime.h"
#include "correction_plugin.h"

#include <AthContainers/AuxElement.h>
#include <xAODTracking/TrackParticle.h>

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace muon_framework {
namespace {
using Value = correction::Variable::Type;
using correction::CorrectionSet;
using Track = xAOD::TrackParticle;
using Muon = xAOD::Muon;
using TrackType = xAOD::Muon::TrackParticleType;
using xAOD::EventInfo;

constexpr size_t production_momentum_size = 12;
constexpr size_t diagnostic_momentum_size = 40;
constexpr uint32_t pt_write = 1, charge_write = 2, id_pt_write = 4, me_pt_write = 8,
                   id_charge_write = 16, me_charge_write = 32;

constexpr std::array<std::string_view, static_cast<size_t>(Entry::count)> entry_names{
    "eta", "pt", "momentum_state", "momentum", "energy", "track", "quality", "isolation",
    "reco", "iso", "ttva", "product", "final",
    "momentum_state_init", "momentum_state_cache_enabled", "momentum_state_release_calls",
    "momentum_state_release_failures", "momentum_state_catalog", "momentum_state_filters",
    "momentum_state_filter_failures", "momentum_state_cache_hits"
};

std::string absolute_file(const char* option) {
    const char* path = std::getenv(option);
    if (!path || !*path || !std::filesystem::path(path).is_absolute()) {
        throw std::runtime_error(std::string("Expected an absolute framework file path in ") + option);
    }
    return std::filesystem::canonical(path).string();
}

struct Runtime {
    correction::PluginRegistry registry;
    std::unique_ptr<CorrectionSet> momentum;
    std::unique_ptr<CorrectionSet> selection;
    std::unique_ptr<CorrectionSet> efficiency;

    Runtime() {
        const auto provider_path = absolute_file("ATLAS_MUON_PROVIDER");
        const auto momentum_path = absolute_file("ATLAS_MUON_MOMENTUM");
        const auto selection_path = absolute_file("ATLAS_MUON_SELECTION");
        const auto efficiency_path = absolute_file("ATLAS_MUON_EFFICIENCY");
        registry.load_library(provider_path);
        momentum = CorrectionSet::from_file(momentum_path, registry);
        selection = CorrectionSet::from_file(selection_path, registry);
        efficiency = CorrectionSet::from_file(efficiency_path, registry);
        const auto size = momentum->at("muon_momentum")->output_size();
        if (size != production_momentum_size && size != diagnostic_momentum_size) {
            throw std::invalid_argument("Unexpected pinned muon momentum output size");
        }
        if (std::getenv("ATLAS_MUON_DISPATCH_AUDIT")) {
            std::cerr << "MUON_B_COMPONENT provider " << provider_path << '\n'
                      << "MUON_B_COMPONENT momentum " << momentum_path << '\n'
                      << "MUON_B_COMPONENT selection " << selection_path << '\n'
                      << "MUON_B_COMPONENT efficiency " << efficiency_path << '\n';
        }
    }
};

const Runtime& runtime() {
    static const Runtime instance;
    return instance;
}

struct DispatchAudit {
    std::array<std::atomic<uint64_t>, entry_names.size()> calls{};
    ~DispatchAudit() {
        for (size_t i = 0; i < calls.size(); ++i) {
            std::cerr << "MUON_B_DISPATCH " << entry_names[i] << ' ' << calls[i].load() << '\n';
        }
    }
};

template<class T>
Value i(T value) { return static_cast<int64_t>(value); }

Value r(double value) { return value; }

const SG::ConstAccessor<std::vector<float>> diag_acc{"definingParametersCovMatrixDiag"};
const SG::ConstAccessor<std::vector<float>> off_diag_acc{"definingParametersCovMatrixOffDiag"};
const SG::ConstAccessor<unsigned int> random_run_acc{"RandomRunNumber"};
const SG::ConstAccessor<float> id_pt_in{"InnerDetectorPt"};
const SG::ConstAccessor<float> me_pt_in{"MuonSpectrometerPt"};

const std::vector<float>* covariance(const Track* track, const SG::ConstAccessor<std::vector<float>>& accessor) {
    return track && accessor.isAvailable(*track) ? &accessor(*track) : nullptr;
}

int64_t length(const std::vector<float>* data) { return data ? static_cast<int64_t>(data->size()) : -1; }

double element(const std::vector<float>* data, size_t index) {
    return data && index < data->size() ? (*data)[index] : 0.;
}

std::array<const Track*, 6> role_tracks(const Muon& muon) {
    return {nullptr,
            muon.trackParticle(TrackType::InnerDetectorTrackParticle),
            muon.trackParticle(TrackType::ExtrapolatedMuonSpectrometerTrackParticle),
            muon.trackParticle(TrackType::CombinedTrackParticle),
            muon.trackParticle(TrackType::MSOnlyExtrapolatedMuonSpectrometerTrackParticle),
            muon.trackParticle(TrackType::MuonSpectrometerTrackParticle)};
}

std::array<size_t, 2> selected_roles(const Muon& muon, const std::array<const Track*, 6>& tracks) {
    std::array<double, 2> roles{};
    selection().at("muon_selection_track_roles")->evaluate_array(
        {i(muon.muonType()),i(tracks[1] != nullptr),i(tracks[2] != nullptr),i(tracks[3] != nullptr),
         i(tracks[4] != nullptr),i(tracks[5] != nullptr)}, roles.data(), roles.size());
    return {static_cast<size_t>(roles[0]), static_cast<size_t>(roles[1])};
}

void append_momentum_track(Values& row, const Track* track) {
    const auto* diag = covariance(track, diag_acc);
    const auto* off_diag = covariance(track, off_diag_acc);
    row.push_back(i(track != nullptr));
    row.push_back(r(track ? track->d0() : 0.));
    row.push_back(r(track ? track->z0() : 0.));
    row.push_back(r(track ? track->phi() : 0.));
    row.push_back(r(track ? track->theta() : 0.));
    row.push_back(r(track ? track->qOverP() : 0.));
    row.push_back(i(length(diag)));
    row.push_back(i(length(off_diag)));
    for (size_t index = 0; index < 5; ++index) row.push_back(r(element(diag, index)));
    for (size_t index = 0; index < 10; ++index) row.push_back(r(element(off_diag, index)));
}

std::array<double, diagnostic_momentum_size> momentum(const Muon& muon, const EventInfo* event,
                                                      const std::string& state) {
    const uint64_t event_number = event ? event->eventNumber() : 0;
    Values row{state, i(event != nullptr), i(event ? event->runNumber() : 0),
               i(event_number & 0xffffffffULL), i(event_number >> 32),
               i(event && event->eventType(EventInfo::IS_SIMULATION)), i(muon.muonType()),
               r(muon.pt()), r(muon.eta()), r(muon.phi()), r(muon.charge())};
    row.reserve(80);
    append_momentum_track(row, event ? muon.trackParticle(TrackType::CombinedTrackParticle) : nullptr);
    append_momentum_track(row, event ? muon.trackParticle(TrackType::InnerDetectorTrackParticle) : nullptr);
    append_momentum_track(row, event ? muon.trackParticle(TrackType::ExtrapolatedMuonSpectrometerTrackParticle) : nullptr);
    const auto evaluator = runtime().momentum->at("muon_momentum");
    std::array<double, diagnostic_momentum_size> outputs{};
    evaluator->evaluate_array(row, outputs.data(), evaluator->output_size());
    return outputs;
}

int read_hit(const Track* track, xAOD::SummaryType field) {
    uint8_t value = 0;
    return track && track->summaryValue(value, field) ? value : -1;
}

template<class Summary>
int read_hit(const Muon& muon, Summary field) {
    uint8_t value = 0;
    return muon.summaryValue(value, field) ? value : -1;
}

bool has_float(const Muon& muon, const SG::ConstAccessor<float>& accessor) {
    return accessor.isAvailable(muon);
}

double read_float(const Muon& muon, const SG::ConstAccessor<float>& accessor) {
    return accessor.isAvailable(muon) ? accessor(muon) : 0.;
}

CP::CorrectionCode code(double value) {
    if (value == CP::CorrectionCode::Ok) return CP::CorrectionCode::Ok;
    if (value == CP::CorrectionCode::OutOfValidityRange) return CP::CorrectionCode::OutOfValidityRange;
    if (value == CP::CorrectionCode::Error) return CP::CorrectionCode::Error;
    throw std::runtime_error("Unexpected CP correction status for a called tool");
}
}

void record_dispatch(Entry entry) {
    static const bool enabled = std::getenv("ATLAS_MUON_DISPATCH_AUDIT") != nullptr
#ifdef ATLAS_MUON_B_ALIAS_PROBE
        || std::getenv("ATLAS_MUON_B_ALIAS_COUNTS") != nullptr
#endif
        ;
    if (enabled) {
        static DispatchAudit audit;
        ++audit.calls.at(static_cast<size_t>(entry));
    }
}

bool configured(std::string_view actual, std::string_view component) {
    if (actual.starts_with("ToolSvc.")) actual.remove_prefix(8);
    return actual == component;
}

std::string state_name(const CP::SystematicSet& state) {
    return state.empty() ? "NOSYS" : state.name();
}

const CorrectionSet& selection() { return *runtime().selection; }
const CorrectionSet& efficiencies() { return *runtime().efficiency; }

double raw_eta_bits(const xAOD::IParticle& particle) {
    return selection().at("muon_selection_eta")->evaluate({r(particle.eta())});
}

double pt_bits(const xAOD::IParticle& particle) {
    return selection().at("muon_selection_pt")->evaluate({r(particle.pt())});
}

float energy(const Muon& muon) {
    return static_cast<float>(momentum(muon, nullptr, "NOSYS")[6]);
}

CP::CorrectionCode apply_momentum(Muon& muon, const EventInfo& event, const std::string& state) {
    static const SG::Accessor<float> pt{"pt"}, charge{"charge"};
    static const SG::Decorator<float> id_pt{"InnerDetectorPt"}, me_pt{"MuonSpectrometerPt"};
    static const SG::Decorator<float> id_charge{"InnerDetectorCharge"}, me_charge{"MuonSpectrometerCharge"};
    const auto out = momentum(muon, &event, state);
    const auto writes = static_cast<uint32_t>(out[2]);
    if (writes & pt_write) pt(muon) = out[3];
    if (writes & charge_write) charge(muon) = out[7];
    if (writes & id_pt_write) id_pt(muon) = out[8];
    if (writes & me_pt_write) me_pt(muon) = out[9];
    if (writes & id_charge_write) id_charge(muon) = out[10];
    if (writes & me_charge_write) me_charge(muon) = out[11];
    return code(out[1]);
}

uint32_t track_bits(const Muon& muon, const EventInfo& event, float pv_z) {
    const auto tracks = role_tracks(muon);
    const auto roles = selected_roles(muon, tracks);
    const auto* primary = tracks.at(roles[0]);
    const auto* diag = covariance(primary, diag_acc);
    const Values row{
        r(muon.pt()), r(muon.eta()), r(muon.phi()), i(roles[0]),
        r(primary ? primary->d0() : 0.), r(primary ? primary->phi() : 0.),
        r(primary ? primary->z0() : 0.), r(primary ? primary->vz() : 0.),
        r(element(diag, 0)), i(length(diag)), r(pv_z),
        r(primary ? event.beamPosSigmaX() : 0.),
        r(primary ? event.beamPosSigmaY() : 0.),
        r(primary ? event.beamPosSigmaXY() : 0.)
    };
    return static_cast<uint32_t>(selection().at("muon_selection_track")->evaluate(row));
}

std::array<double, 2> quality(const Muon& muon) {
    static const SG::ConstAccessor<uint16_t> all_authors{"allAuthors"};
    static const SG::ConstAccessor<float> score{"CaloMuonScore"};
    static const SG::ConstAccessor<float> balance{"momentumBalanceSignificance"};
    static const SG::ConstAccessor<float> curvature{"scatteringCurvatureSignificance"};
    static const SG::ConstAccessor<float> neighbour{"scatteringNeighbourSignificance"};
    const auto tracks = role_tracks(muon);
    const auto roles = selected_roles(muon, tracks);
    const auto* id = tracks[1];
    const auto* me = tracks[2];
    const auto* cb = tracks[3];
    const auto* hits_track = tracks.at(roles[1]);
    const auto* id_diag = covariance(id, diag_acc);
    const auto* me_diag = covariance(me, diag_acc);
    const auto* cb_diag = covariance(cb, diag_acc);
    const auto parameters = (has_float(muon, balance) ? 1 : 0) | (has_float(muon, curvature) ? 2 : 0) |
                            (has_float(muon, neighbour) ? 4 : 0);
    const Values row{
        r(muon.pt()), r(muon.eta()), r(read_float(muon, id_pt_in)), r(read_float(muon, me_pt_in)),
        i(has_float(muon, id_pt_in) && has_float(muon, me_pt_in)),
        i(muon.muonType()), i(muon.author()), i(all_authors.isAvailable(muon) ? all_authors(muon) : -1),
        i(roles[0]), i(id != nullptr), i(me != nullptr), i(cb != nullptr),
        i(read_hit(hits_track, xAOD::numberOfPixelHits)), i(read_hit(hits_track, xAOD::numberOfPixelDeadSensors)),
        i(read_hit(hits_track, xAOD::numberOfSCTHits)), i(read_hit(hits_track, xAOD::numberOfSCTDeadSensors)),
        i(read_hit(hits_track, xAOD::numberOfPixelHoles)), i(read_hit(hits_track, xAOD::numberOfSCTHoles)),
        r(id ? id->qOverP() : 0.), r(me ? me->qOverP() : 0.), r(cb ? cb->qOverP() : 0.),
        r(id ? id->theta() : 0.), r(me ? me->theta() : 0.), r(cb ? cb->theta() : 0.), r(cb ? cb->phi() : 0.),
        r(element(id_diag, 4)), r(element(me_diag, 4)), r(element(cb_diag, 4)),
        i(length(id_diag)), i(length(me_diag)), i(length(cb_diag)),
        i(read_hit(muon, xAOD::numberOfPrecisionLayers)),
        i(read_hit(muon, xAOD::numberOfPrecisionHoleLayers)),
        i(read_hit(muon, xAOD::numberOfGoodPrecisionLayers)),
        i(read_hit(muon, xAOD::MuonSummaryType::innerSmallHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::innerLargeHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::middleSmallHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::middleLargeHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::outerSmallHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::outerLargeHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::extendedSmallHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::extendedLargeHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::extendedSmallHoles)),
        i(read_hit(muon, xAOD::MuonSummaryType::isSmallGoodSectors)),
        i(read_hit(muon, xAOD::MuonSummaryType::etaLayer1STGCHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::etaLayer2STGCHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::MMHits)),
        i(read_hit(muon, xAOD::MuonSummaryType::combinedTrackOutBoundsPrecisionHits)),
        r(read_float(muon, score)), i(has_float(muon, score)), i(parameters)
    };
    std::array<double, 2> result{};
    selection().at("muon_selection_quality")->evaluate_array(row, result.data(), result.size());
    return result;
}

bool isolation(const Muon& muon) {
    static const SG::ConstAccessor<float> track_iso{"ptvarcone30_Nonprompt_All_MaxWeightTTVA_pt500"};
    static const SG::ConstAccessor<float> neutral_iso{"neflowisol20"};
    const unsigned mask = (has_float(muon, track_iso) ? 1 : 0) | (has_float(muon, neutral_iso) ? 2 : 0);
    return selection().at("muon_selection_isolation")->evaluate(
        {r(muon.pt()), r(read_float(muon, track_iso)), r(read_float(muon, neutral_iso)), i(mask)}) == 1.;
}

bool final_selection(const Muon& muon) {
    static const SG::ConstAccessor<uint32_t> eta{"selectEta"}, pt{"selectPt"}, track{"trackSelection_medium"};
    static const SG::ConstAccessor<char> medium{"good_muon_medium"}, iso{"isolated_muon_medium"};
    return selection().at("muon_selection_final")->evaluate(
        {i(eta(muon)), i(pt(muon)), i(track(muon)), i(medium(muon)), i(iso(muon))}) == 1.;
}

CP::CorrectionCode scale_factor(std::string_view correction, const std::string& state,
                                const Muon& muon, const EventInfo& event, float& result) {
    const auto run_number = random_run_acc.isAvailable(event) ? random_run_acc(event) : 0;
    std::array<double, 2> output{};
    efficiencies().at(std::string(correction))->evaluate_array(
        {state, i(run_number), r(muon.pt()), r(muon.eta()), r(muon.phi()), r(muon.charge()), i(muon.muonType()), i(1)},
        output.data(), output.size());
    result = output[0];
    return code(output[1]);
}

float product(const std::array<float, 3>& factors) {
    return static_cast<float>(efficiencies().at("muon_eff_product")->evaluate(
        {r(factors[0]), r(factors[1]), r(factors[2])}));
}
}
