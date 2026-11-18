#include <AnaAlgorithm/AnaAlgorithm.h>
#include <AsgTools/AsgComponentFactories.h>
#include <AsgTools/ToolStore.h>
#include <MuonAnalysisInterfaces/IMuonEfficiencyScaleFactors.h>
#include <SelectionHelpers/SysReadSelectionHandle.h>
#include <SystematicsHandles/SysReadDecorHandle.h>
#include <SystematicsHandles/SysReadHandle.h>
#include <SystematicsHandles/SysWriteDecorHandle.h>
#include <xAODEventInfo/EventInfo.h>
#include <xAODMuon/MuonContainer.h>

#include <bit>
#include <cstdint>

namespace Reference {

class MuonEfficiencyStatusObserver final : public EL::AnaAlgorithm {
public:
    using EL::AnaAlgorithm::AnaAlgorithm;

    StatusCode initialize() override {
        m_tool = asg::ToolStore::get<CP::IMuonEfficiencyScaleFactors>(m_toolName.value());
        if (!m_tool) {
            ANA_MSG_ERROR("Cannot find the initialized release tool " << m_toolName);
            return StatusCode::FAILURE;
        }
        ANA_CHECK(m_muons.initialize(m_states));
        ANA_CHECK(m_eventInfo.initialize(m_states));
        ANA_CHECK(m_preselection.initialize(m_states, m_muons));
        ANA_CHECK(m_originalScaleFactor.initialize(m_states, m_muons));
        ANA_CHECK(m_status.initialize(m_states, m_muons));
        ANA_CHECK(m_states.addSystematics(*m_tool));
        ANA_CHECK(m_states.initialize());
        return StatusCode::SUCCESS;
    }

    StatusCode execute(const EventContext& ctx) override {
        for (const auto& state : m_states.systematicsVector()) {
            ANA_CHECK(m_tool->applySystematicVariation(state));
            const xAOD::MuonContainer* muons = nullptr;
            const xAOD::EventInfo* event = nullptr;
            ANA_CHECK(m_muons.retrieve(muons, state, ctx));
            ANA_CHECK(m_eventInfo.retrieve(event, state, ctx));
            for (const xAOD::Muon* muon : *muons) {
                int code = -1;
                float replay = CP::invalidScaleFactor();
                if (m_preselection.getBool(*muon, state)) {
                    replay = 0;
                    code = m_tool->getEfficiencyScaleFactor(*muon, replay, event).code();
                }
                const float original = m_originalScaleFactor.get(*muon, state);
                if (code == CP::CorrectionCode::Error ||
                    std::bit_cast<std::uint32_t>(replay) != std::bit_cast<std::uint32_t>(original)) {
                    ANA_MSG_ERROR("Release SF replay mismatch for event " << event->eventNumber()
                        << " muon " << muon->index() << " state " << state.name()
                        << " original " << original << " replay " << replay << " code " << code);
                    return StatusCode::FAILURE;
                }
                m_status.set(*muon, code, state);
            }
        }
        ANA_CHECK(m_tool->applySystematicVariation(CP::SystematicSet{}));
        return StatusCode::SUCCESS;
    }

private:
    Gaudi::Property<std::string> m_toolName{this, "originalTool", "", "Full name of the initialized release tool"};
    CP::IMuonEfficiencyScaleFactors* m_tool = nullptr;
    CP::SysListHandle m_states{this};
    CP::SysReadHandle<xAOD::MuonContainer> m_muons{this, "muons", "AnaMuons_%SYS%", "Calibrated muons"};
    CP::SysReadHandle<xAOD::EventInfo> m_eventInfo{this, "eventInfo", "EventInfo", "Release event info"};
    CP::SysReadSelectionHandle m_preselection{this, "preselection", "", "The original scale-factor preselection"};
    CP::SysReadDecorHandle<float> m_originalScaleFactor{this, "originalScaleFactor", "", "The scale factor produced by the original algorithm"};
    CP::SysWriteDecorHandle<int> m_status{this, "status", "", "Raw CorrectionCode or -1 for a skipped call"};
};

} // namespace Reference

DECLARE_COMPONENT(Reference::MuonEfficiencyStatusObserver)
