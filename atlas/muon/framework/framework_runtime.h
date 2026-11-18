#ifndef ATLAS_MUON_FRAMEWORK_RUNTIME_H
#define ATLAS_MUON_FRAMEWORK_RUNTIME_H

#include "correction.h"
#include <PATInterfaces/CorrectionCode.h>
#include <PATInterfaces/SystematicSet.h>
#include <xAODEventInfo/EventInfo.h>
#include <xAODMuon/Muon.h>

#include <array>
#include <cstdint>
#include <string_view>

namespace muon_framework {
using Values = std::vector<correction::Variable::Type>;

enum class Entry {
    eta, pt, momentum_state, momentum, energy, track, quality,
    isolation, reco, iso, ttva, product, final,
    momentum_state_init, momentum_state_cache_enabled, momentum_state_release_calls,
    momentum_state_release_failures, momentum_state_catalog, momentum_state_filters,
    momentum_state_filter_failures, momentum_state_cache_hits, count
};

void record_dispatch(Entry entry);
bool configured(std::string_view actual, std::string_view component);
std::string state_name(const CP::SystematicSet& state);

const correction::CorrectionSet& selection();
const correction::CorrectionSet& efficiencies();
double raw_eta_bits(const xAOD::IParticle& particle);
double pt_bits(const xAOD::IParticle& particle);
float energy(const xAOD::Muon& muon);
CP::CorrectionCode apply_momentum(xAOD::Muon& muon, const xAOD::EventInfo& event, const std::string& state);
uint32_t track_bits(const xAOD::Muon& muon, const xAOD::EventInfo& event, float pv_z);
std::array<double, 2> quality(const xAOD::Muon& muon);
bool isolation(const xAOD::Muon& muon);
bool final_selection(const xAOD::Muon& muon);
CP::CorrectionCode scale_factor(std::string_view correction, const std::string& state,
                                const xAOD::Muon& muon, const xAOD::EventInfo& event, float& result);
float product(const std::array<float, 3>& factors);
}

#endif
