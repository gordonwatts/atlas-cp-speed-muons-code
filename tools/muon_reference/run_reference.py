#!/usr/bin/env python3
import os

from AnalysisAlgorithmsConfig.EventLoopCPRunScript import EventLoopCPRunScript


class MuonReferenceRunner(EventLoopCPRunScript):
    """Configure the release tool properties absent from the CP YAML block."""

    def modifyYamlConfig(self):
        if os.environ.get("MUON_REFERENCE_OUTPUT") == "production":
            self.rawConfig.pop("PrintConfiguration", None)

    def modifyAlgSequence(self):
        algorithms = [
            alg for alg in self.algSeq if alg.type() == "CP::MuonCalibrationAndSmearingAlg"
        ]
        (momentum,) = algorithms
        momentum.calibrationAndSmearingTool.systematicScheme = os.environ["MUON_MOMENTUM_SCHEME"]
        momentum.calibrationAndSmearingTool.doEtaSagittaSys = os.environ["MUON_ETA_SAGITTA"] == "1"

        (ntuple,) = [alg for alg in self.algSeq if alg.type() == "CP::AsgxAODNTupleMakerAlg"]
        if os.environ.get("MUON_REFERENCE_OUTPUT") == "production":
            targets = {"mu_pt_%SYS%", "mu_effSF_medium_%SYS%", "mu_select_medium_%SYS%"}
            ntuple.Branches = [branch for branch in ntuple.Branches if branch.split("->", maxsplit=1)[1].split()[0] in targets]

        if os.environ.get("MUON_VALIDATION_OUTPUT") == "1":
            momentum.outOfValidityDeco = "muonCalib_valid"
            branches = {
                "eta_gate": ("selectEta", "uint32"),
                "pt_gate": ("selectPt", "uint32"),
                "track_gate": ("trackSelection_medium", "uint32"),
                "quality_gate": ("good_muon_medium", "char"),
                "isolation_gate": ("isolated_muon_medium", "char"),
                "bad_muon": ("is_bad_medium", "char"),
                "calibration_valid": ("muonCalib_valid", "uint32"),
                "id_pt": ("InnerDetectorPt", "float"),
                "me_pt": ("MuonSpectrometerPt", "float"),
                "id_charge": ("InnerDetectorCharge", "float"),
                "me_charge": ("MuonSpectrometerCharge", "float"),
                "charge": ("charge", "float"),
            }
            ntuple.Branches = list(ntuple.Branches) + [
                f"AnaMuons_%SYS%.{aux} -> mu_detail_{name}_%SYS% type={dtype}"
                for name, (aux, dtype) in branches.items()
            ]

            path = os.environ.get("MUON_STATUS_OBSERVER_LIBRARY")
            if path:
                self.addEfficiencyObserver(ntuple, path)

    def addEfficiencyObserver(self, ntuple, path):
        import ROOT
        from AnaAlgorithm.DualUseConfig import createAlgorithm

        if ROOT.gSystem.Load(path) < 0:
            raise RuntimeError(f"Could not load the status observer at {path}")

        efficiency_algs = [alg for alg in self.algSeq if alg.type() == "CP::MuonEfficiencyScaleFactorAlg"]
        if len(efficiency_algs) != 3:
            raise RuntimeError(f"Expected three original efficiency algorithms, found {len(efficiency_algs)}")

        ntuple_idx = next(index for index, alg in enumerate(self.algSeq) if alg is ntuple)
        branches = []
        for offset, original in enumerate(efficiency_algs):
            source = original.scaleFactorDecoration
            key = source.split("_")[1]
            observer = createAlgorithm("Reference::MuonEfficiencyStatusObserver", f"ObserveMuon{key}SFStatus")
            observer.originalTool = original.name() + ".efficiencyScaleFactorTool"
            observer.muons = original.muons
            observer.preselection = original.preselection
            observer.originalScaleFactor = source
            observer.status = f"muon_{key}_status_medium_%SYS%"
            self.algSeq.insert(ntuple_idx + offset, observer)
            branches.append(f"{original.muons}.{observer.status} -> mu_detail_{key}_sf_status_%SYS% type=int")
        ntuple.Branches = list(ntuple.Branches) + branches


if __name__ == "__main__":
    MuonReferenceRunner().run()
