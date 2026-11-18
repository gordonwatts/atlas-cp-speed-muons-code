#!/usr/bin/env python3
import os

from AnalysisAlgorithmsConfig.EventLoopCPRunScript import EventLoopCPRunScript


class MuonReferenceRunner(EventLoopCPRunScript):
    """Configure the release tool properties absent from the CP YAML block."""

    def modifyAlgSequence(self):
        algorithms = [
            alg for alg in self.algSeq if alg.type() == "CP::MuonCalibrationAndSmearingAlg"
        ]
        (momentum,) = algorithms
        momentum.calibrationAndSmearingTool.systematicScheme = os.environ["MUON_MOMENTUM_SCHEME"]
        momentum.calibrationAndSmearingTool.doEtaSagittaSys = os.environ["MUON_ETA_SAGITTA"] == "1"

        if os.environ.get("MUON_VALIDATION_OUTPUT") == "1":
            momentum.outOfValidityDeco = "muonCalib_valid"
            (ntuple,) = [alg for alg in self.algSeq if alg.type() == "CP::AsgxAODNTupleMakerAlg"]
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


if __name__ == "__main__":
    MuonReferenceRunner().run()
