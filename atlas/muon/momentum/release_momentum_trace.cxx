#include <MuonMomentumCorrections/MuonCalibTool.h>
#include <xAODEventInfo/EventInfo.h>
#include <xAODMuon/MuonContainer.h>
#include <xAODRootAccess/Init.h>
#include <xAODRootAccess/TEvent.h>

#include <TFile.h>

#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

class ReleaseMomentumTrace : public CP::MuonCalibTool {
public:
    using CP::MuonCalibTool::MuonCalibTool;

    void trace(std::ostream& out, const xAOD::Muon& muon, const xAOD::EventInfo& event) const {
        auto object = convertToMuonObj(columnar::MuonId(muon), columnar::EventInfoId(event));
        const unsigned int seed = 1 + std::abs(object.CB.phi) * 1E6 + std::abs(object.CB.eta) * 1E3 + event.eventNumber();
        out << seed << '\t' << object.rnd_g0 << '\t' << object.rnd_g1 << '\t' << object.rnd_g2
            << '\t' << object.rnd_g3 << '\t' << object.rnd_g4 << '\t' << object.rnd_g_highPt
            << '\t' << object.CB.uncalib_pt << '\t' << object.ID.uncalib_pt << '\t' << object.ME.uncalib_pt
            << '\t' << object.expectedPercentResID << '\t' << object.expectedPercentResME
            << '\t' << object.expectedResID << '\t' << object.expectedResME;
        const auto scale = m_MuonIntScaleSmearTool->applyCorrection(object);
        if (scale != CP::CorrectionCode::Ok) throw std::runtime_error("Release scale and smear returned non-Ok");
        out << '\t' << object.CB.calib_pt << '\t' << object.ID.calib_pt << '\t' << object.ME.calib_pt;
        const bool sagitta = muon.muonType() != xAOD::Muon::SiliconAssociatedForwardMuon;
        if (sagitta) {
            const auto result = m_MuonIntSagittaTool->applyCorrection(object);
            if (result != CP::CorrectionCode::Ok) throw std::runtime_error("Release sagitta returned non-Ok");
        }
        const auto unused = std::numeric_limits<double>::quiet_NaN();
        out << '\t' << sagitta << '\t' << (sagitta ? object.ID.calib_pt : unused)
            << '\t' << (sagitta ? object.ME.calib_pt : unused) << '\t' << object.CB.calib_pt << '\n';
    }
};

int main(int argc, char** argv) {
    if (argc != 3) throw std::runtime_error("usage: release_momentum_trace ORIGINAL.root OUTPUT.tsv");
    if (xAOD::Init("ReleaseMuonMomentumTrace").isFailure()) return 1;
    std::unique_ptr<TFile> input(TFile::Open(argv[1]));
    xAOD::TEvent reader(xAOD::TEvent::kClassAccess);
    if (!input || input->IsZombie() || reader.readFrom(input.get()).isFailure()) return 1;

    ReleaseMomentumTrace tool("releaseMuonMomentumTrace");
    if (tool.setProperty("calibMode", 0).isFailure() || tool.setProperty("IsRun3Geo", true).isFailure() ||
        tool.setProperty("systematicScheme", std::string("AllSys")).isFailure() ||
        tool.setProperty("doEtaSagittaSys", true).isFailure() || tool.initialize().isFailure()) return 1;
    const auto recommended = tool.recommendedSystematics();
    if (recommended.size() != 28) return 1;
    std::vector<CP::SystematicSet> states{CP::SystematicSet{}};
    for (const auto& state : recommended) states.emplace_back(CP::SystematicSet{state});

    std::ofstream out(argv[2]);
    out << std::setprecision(std::numeric_limits<double>::max_digits10);
    out << "entry\tindex\tstate\troot_seed\tg0\tg1\tg2\tg3\tg4\tg_high_pt"
        << "\traw_cb_gev\traw_id_gev\traw_me_gev\texpected_percent_res_id\texpected_percent_res_me"
        << "\texpected_res_id\texpected_res_me\tscale_cb_gev\tscale_id_gev\tscale_me_gev"
        << "\tsagitta_called\tsagitta_id_gev\tsagitta_me_gev\tpre_override_cb_gev\n";
    for (long long entry = 0; entry < 100; ++entry) {
        if (reader.getEntry(entry) < 0) return 1;
        const xAOD::MuonContainer* muons = nullptr;
        const xAOD::EventInfo* info = nullptr;
        if (reader.retrieve(muons, "AnalysisMuons").isFailure() || reader.retrieve(info, "EventInfo").isFailure()) return 1;
        for (const auto& state : states) {
            if (tool.applySystematicVariation(state).isFailure()) return 1;
            for (const auto* muon : *muons) {
                if (!std::isfinite(muon->eta()) || std::abs(muon->eta()) > float(2.7)) continue;
                out << entry << '\t' << muon->index() << '\t' << (state.empty() ? "NOSYS" : state.name()) << '\t';
                tool.trace(out, *muon, *info);
            }
        }
    }
    out.close();
    return !out;
}
