#include "framework_runtime.h"
#include "release_symbols.h"

#include <AsgAnalysisAlgorithms/AsgEnergyDecoratorAlg.h>
#include <AsgAnalysisAlgorithms/AsgLeptonTrackSelectionAlg.h>
#include <AsgAnalysisAlgorithms/AsgObjectScaleFactorAlg.h>
#include <AsgAnalysisAlgorithms/AsgPtEtaSelectionTool.h>
#include <AsgAnalysisAlgorithms/AsgSelectionAlg.h>
#include <AsgDataHandles/ReadHandle.h>
#include <MuonAnalysisAlgorithms/MuonIsolationAlg.h>
#include <MuonAnalysisAlgorithms/MuonSelectionAlg.h>
#include <MuonEfficiencyCorrections/EffiCollection.h>
#include <MuonEfficiencyCorrections/MuonEfficiencyScaleFactors.h>
#include <MuonMomentumCorrections/MuonCalibTool.h>
#include <SelectionHelpers/SelectionHelpers.h>

#include <dlfcn.h>
#include <array>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <unordered_map>

namespace {
using namespace muon_framework;
using namespace muon_framework::release_symbols;

constexpr std::string_view eta_tool = "seq1_MuonEtaCutAlg_AnaMuons.selectionTool";
constexpr std::string_view pt_tool = "seq1_MuonPtCutAlg_AnaMuons.selectionTool";
constexpr std::string_view momentum_tool = "seq1_MuonCalibrationAndSmearingAlg_AnaMuons.calibrationAndSmearingTool";
constexpr std::string_view energy_alg = "seq1_EnergyDecorator_AnaMuons";
constexpr std::string_view track_alg = "seq1_MuonTrackSelectionAlg_AnaMuons_medium";
constexpr std::string_view quality_alg = "seq1_MuonSelectionAlg_AnaMuons_medium";
constexpr std::string_view isolation_alg = "seq1_MuonIsolationAlg_AnaMuons_medium";
constexpr std::string_view product_alg = "seq1_MuonCombinedEfficiencyScaleFactorAlg_AnaMuons_medium";
constexpr std::string_view final_alg = "seq1_ObjectSelectionSummary_AnaMuons_medium_analysis";

struct EfficiencyRoute {
    std::string_view tool, working_point, correction;
    Entry audit;
};

constexpr std::array routes{
    EfficiencyRoute{"seq1_MuonEfficiencyScaleFactorAlgReco_AnaMuons_medium.efficiencyScaleFactorTool",
                    "Medium", "muon_eff_reco", Entry::reco},
    EfficiencyRoute{"seq1_MuonEfficiencyScaleFactorAlgIsol_AnaMuons_medium.efficiencyScaleFactorTool",
                    "PflowTight_VarRadIso", "muon_eff_iso", Entry::iso},
    EfficiencyRoute{"seq1_MuonEfficiencyScaleFactorAlgTTVA_AnaMuons_medium.efficiencyScaleFactorTool",
                    "TTVA", "muon_eff_ttva", Entry::ttva}
};

// CP's tool state is applied and consumed in order on the same analysis thread.
thread_local std::unordered_map<const CP::MuonCalibTool*, std::string> momentum_states;

template<class Function>
Function original(const char* symbol) {
    dlerror();
    void* address = dlsym(RTLD_NEXT, symbol);
    const char* error = dlerror();
    if (!address || error) {
        throw std::runtime_error(std::string("Cannot resolve original AnalysisBase symbol ") + symbol);
    }
    static_assert(sizeof(Function) == sizeof(void*));
    Function function;
    std::memcpy(&function, &address, sizeof(function));
    return function;
}

const xAOD::Muon& as_muon(const xAOD::IParticle& particle) {
    const auto* muon = dynamic_cast<const xAOD::Muon*>(&particle);
    if (!muon) throw std::runtime_error("Frozen muon algorithm received a different object type");
    return *muon;
}
}

namespace CP {
asg::AcceptData AsgPtEtaSelectionTool::accept(const xAOD::IParticle* particle) const {
    const bool is_eta = configured(name(), eta_tool);
    const bool is_pt = configured(name(), pt_tool);
    if (!is_eta && !is_pt) {
        using Signature = asg::AcceptData (*)(const AsgPtEtaSelectionTool*, const xAOD::IParticle*);
        static const auto fallback = original<Signature>(pt_eta_accept);
        return fallback(this, particle);
    }
    if (!particle || m_useClusterEta || m_useDressedProperties || m_useConstituentMomentum ||
        m_maxPt != 0 || m_minEta != 0 || m_etaGapLow != 0 || m_etaGapHigh != 0 ||
        m_minRapidity != 0 || m_maxRapidity != 0 ||
        m_maxEta != (is_eta ? 2.7F : 0.F) || m_minPt != (is_pt ? 3000.F : 0.F)) {
        throw std::runtime_error("Unexpected frozen muon eta/pT cut configuration");
    }
    record_dispatch(is_eta ? Entry::eta : Entry::pt);
    const uint32_t bits = is_eta ? raw_eta_bits(as_muon(*particle)) : pt_bits(as_muon(*particle));
    asg::AcceptData result(&m_accept);
    result.setCutResult(is_eta ? m_maxEtaCutIndex : m_minPtCutIndex, (bits & 1U) != 0);
    return result;
}

StatusCode MuonCalibTool::applySystematicVariation(const CP::SystematicSet& state) {
    using Signature = StatusCode (*)(MuonCalibTool*, const CP::SystematicSet&);
    static const auto release = original<Signature>(momentum_apply_state);
    auto status = release(this, state);
    if (!configured(name(), momentum_tool)) return status;
    if (!status.isSuccess()) return status;
    record_dispatch(Entry::momentum_state);
    CP::SystematicSet filtered;
    if (!CP::SystematicSet::filterForAffectingSystematics(state, affectingSystematics(), filtered)) {
        return StatusCode::FAILURE;
    }
    momentum_states[this] = state_name(filtered);
    return status;
}

CP::CorrectionCode MuonCalibTool::applyCorrection(xAOD::Muon& muon) const {
    if (!configured(name(), momentum_tool)) {
        using Signature = CP::CorrectionCode (*)(const MuonCalibTool*, xAOD::Muon&);
        static const auto fallback = original<Signature>(momentum_apply);
        return fallback(this, muon);
    }
    if (!m_isRun3 || m_calibMode != 0 || m_sysScheme.value() != "AllSys" || !m_doEtaSagittaSys ||
        m_validationMode || m_useRndRun || m_extra_highpt_smearing || m_2stations_highpt_smearing) {
        throw std::runtime_error("Unexpected frozen muon momentum configuration");
    }
    SG::ReadHandle<xAOD::EventInfo> event(m_eventInfo);
    if (!event.isValid() || !event->eventType(xAOD::EventInfo::IS_SIMULATION)) {
        throw std::runtime_error("Frozen muon momentum tool requires the MC EventInfo");
    }
    auto state = momentum_states.find(this);
    if (state == momentum_states.end()) {
        throw std::runtime_error("Frozen muon momentum tool did not bind its original systematic setter");
    }
    record_dispatch(Entry::momentum);
    return apply_momentum(muon, *event, state->second);
}

CP::CorrectionCode MuonEfficiencyScaleFactors::getEfficiencyScaleFactor(
    const xAOD::Muon& muon, float& sf, const xAOD::EventInfo* event) const {
    for (const auto& route : routes) {
        if (!configured(name(), route.tool)) continue;
        if (!m_current_sf || m_wp != route.working_point || m_calibration_version != "251211_Preliminary_r24run3" ||
            !m_breakDownSyst || !m_applyKineDepSys || uncorrelate_sys() || use_lrt() ||
            use_2D_iso_corrections() || !m_custom_dir.empty() ||
            !m_custom_file_Combined.empty() || !m_custom_file_Calo.empty() ||
            !m_custom_file_HighEta.empty() || !m_custom_file_LowPt.empty() ||
            !m_custom_file_LowPtCalo.empty()) {
            throw std::runtime_error("Unexpected frozen muon efficiency configuration");
        }
        const auto* selected = m_current_sf->getSystSet();
        if (!selected) throw std::runtime_error("Missing release efficiency systematic collection");
        auto call = [&](const xAOD::EventInfo& info) {
            if (!info.eventType(xAOD::EventInfo::IS_SIMULATION)) {
                throw std::runtime_error("Frozen muon efficiency tool requires the MC EventInfo");
            }
            record_dispatch(route.audit);
            return scale_factor(route.correction, state_name(*selected), muon, info, sf);
        };
        if (event) return call(*event);
        SG::ReadHandle<xAOD::EventInfo> stored_event(m_eventInfo);
        if (!stored_event.isValid()) return CP::CorrectionCode::Error;
        return call(*stored_event);
    }
    using Signature = CP::CorrectionCode (*)(const MuonEfficiencyScaleFactors*, const xAOD::Muon&,
                                               float&, const xAOD::EventInfo*);
    static const auto fallback = original<Signature>(efficiency_get);
    return fallback(this, muon, sf, event);
}

StatusCode AsgEnergyDecoratorAlg::execute(const EventContext& ctx) {
    if (!configured(name(), energy_alg)) {
        using Signature = StatusCode (*)(AsgEnergyDecoratorAlg*, const EventContext&);
        static const auto fallback = original<Signature>(energy_execute);
        return fallback(this, ctx);
    }
    record_dispatch(Entry::energy);
    for (const auto& sys : m_systematicsList.systematicsVector()) {
        const xAOD::IParticleContainer* particles = nullptr;
        ANA_CHECK(m_particlesHandle.retrieve(particles, sys, ctx));
        for (const auto* particle : *particles) m_energyDecor.set(*particle, energy(as_muon(*particle)), sys);
    }
    return StatusCode::SUCCESS;
}

StatusCode AsgLeptonTrackSelectionAlg::execute(const EventContext& ctx) const {
    if (!configured(name(), track_alg)) {
        using Signature = StatusCode (*)(const AsgLeptonTrackSelectionAlg*, const EventContext&);
        static const auto fallback = original<Signature>(track_execute);
        return fallback(this, ctx);
    }
    if (m_maxD0Significance != 3.F || m_maxDeltaZ0SinTheta != .5F ||
        m_nMinPixelHits != -1 || m_nMaxPixelHits != -1 || m_nMinSCTHits != -1 || m_nMaxSCTHits != -1) {
        throw std::runtime_error("Unexpected frozen muon track selection configuration");
    }
    record_dispatch(Entry::track);
    SG::ReadHandle<xAOD::EventInfo> event(m_eventInfoKey, ctx);
    SG::ReadHandle<xAOD::VertexContainer> vertices(m_primaryVerticesKey, ctx);
    if (!vertices.isValid() || !event.isValid()) return StatusCode::FAILURE;
    const auto vertex_correction = selection().at("muon_selection_vertex");
    float pv_z = 0.F;
    for (const auto* vertex : *vertices) {
        if (vertex_correction->evaluate({static_cast<int64_t>(vertex->vertexType())}) == 1.) {
            pv_z = vertex->z();
            break;
        }
    }
    const auto blank = selectionFromAccept(asg::AcceptData(&m_accept));
    for (const auto& sys : m_systematicsList.systematicsVector()) {
        const xAOD::IParticleContainer* particles = nullptr;
        ANA_CHECK(m_particlesHandle.retrieve(particles, sys, ctx));
        for (const auto* particle : *particles) {
            const auto bits = m_preselection.getBool(*particle, sys) ?
                                  track_bits(as_muon(*particle), *event, pv_z) : blank;
            m_selectionHandle.setBits(*particle, bits, sys);
        }
    }
    return StatusCode::SUCCESS;
}

StatusCode MuonSelectionAlgV2::execute(const EventContext& ctx) {
    if (!configured(name(), quality_alg)) {
        using Signature = StatusCode (*)(MuonSelectionAlgV2*, const EventContext&);
        static const auto fallback = original<Signature>(quality_execute);
        return fallback(this, ctx);
    }
    record_dispatch(Entry::quality);
    for (const auto& sys : m_systematicsList.systematicsVector()) {
        const xAOD::MuonContainer* muons = nullptr;
        ANA_CHECK(m_muonsHandle.retrieve(muons, sys, ctx));
        for (const auto* muon : *muons) {
            if (m_preselection.getBool(*muon, sys)) {
                const auto result = quality(*muon);
                m_selectionHandle.setBool(*muon, result[0] == 1., sys);
                if (m_badMuonVetoHandle) m_badMuonVetoHandle.setBool(*muon, result[1] == 1., sys);
            } else {
                m_selectionHandle.setBits(*muon, m_setOnFail, sys);
                if (m_badMuonVetoHandle) m_badMuonVetoHandle.setBool(*muon, false, sys);
            }
        }
    }
    return StatusCode::SUCCESS;
}

StatusCode MuonIsolationAlg::execute(const EventContext& ctx) {
    if (!configured(name(), isolation_alg)) {
        using Signature = StatusCode (*)(MuonIsolationAlg*, const EventContext&);
        static const auto fallback = original<Signature>(isolation_execute);
        return fallback(this, ctx);
    }
    record_dispatch(Entry::isolation);
    for (const auto& sys : m_systematicsList.systematicsVector()) {
        const xAOD::MuonContainer* muons = nullptr;
        ANA_CHECK(m_muonHandle.retrieve(muons, sys, ctx));
        for (const auto* muon : *muons) {
            if (m_preselection.getBool(*muon, sys)) {
                m_isolationHandle.setBool(*muon, isolation(*muon), sys);
            } else {
                m_isolationHandle.setBits(*muon, m_setOnFail, sys);
            }
        }
    }
    return StatusCode::SUCCESS;
}

StatusCode AsgObjectScaleFactorAlg::execute(const EventContext& ctx) {
    if (!configured(name(), product_alg)) {
        using Signature = StatusCode (*)(AsgObjectScaleFactorAlg*, const EventContext&);
        static const auto fallback = original<Signature>(product_execute);
        return fallback(this, ctx);
    }
    const std::vector<std::string> frozen_factors{
        "muon_reco_effSF_medium_%SYS%", "muon_isol_effSF_medium_%SYS%", "muon_TTVA_effSF_medium_%SYS%"
    };
    if (m_inScaleFactors.value() != frozen_factors || m_inSFHandles.size() != 3) {
        throw std::runtime_error("Unexpected release product factor order");
    }
    record_dispatch(Entry::product);
    for (const auto& sys : m_systematicsList.systematicsVector()) {
        const xAOD::IParticleContainer* particles = nullptr;
        ANA_CHECK(m_particlesHandle.retrieve(particles, sys, ctx));
        for (const auto* particle : *particles) {
            const std::array<float, 3> factors{m_inSFHandles[0].get(*particle, sys),
                                               m_inSFHandles[1].get(*particle, sys),
                                               m_inSFHandles[2].get(*particle, sys)};
            m_outSFHandle.set(*particle, product(factors), sys);
        }
    }
    return StatusCode::SUCCESS;
}

StatusCode AsgSelectionAlg::execute(const EventContext& ctx) {
    if (!configured(name(), final_alg)) {
        using Signature = StatusCode (*)(AsgSelectionAlg*, const EventContext&);
        static const auto fallback = original<Signature>(final_execute);
        return fallback(this, ctx);
    }
    if (!m_selectionTool.empty() || m_systematicsTool) {
        throw std::runtime_error("Unexpected final selection tools");
    }
    record_dispatch(Entry::final);
    for (const auto& sys : m_systematicsList.systematicsVector()) {
        const xAOD::IParticleContainer* particles = nullptr;
        ANA_CHECK(m_particlesHandle.retrieve(particles, sys, ctx));
        for (const auto* particle : *particles) {
            m_selectionHandle.setBool(*particle, final_selection(as_muon(*particle)), sys);
        }
    }
    return StatusCode::SUCCESS;
}
}
