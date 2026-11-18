#!/usr/bin/env bash

export ATLAS_LOCAL_ROOT_BASE=/cvmfs/atlas.cern.ch/repo/ATLASLocalRootBase
source "$ATLAS_LOCAL_ROOT_BASE/user/atlasLocalSetup.sh" --quiet || return
asetup AnalysisBase,25.2.117 || return
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 BLIS_NUM_THREADS=1 ROOT_MAX_THREADS=1
export XAOD_ACCESSTRACER_FRACTION=0
export CAMPAIGN=/home/gwatts/atlas-cp-speed/muon-skill-c8a2374-20261117
export RESULTS="$CAMPAIGN/results/checkpoint-01"
export INPUT=/cvmfs/atlas-nightlies.cern.ch/repo/data/data-art/ASG/DAOD_PHYSLITE/p7266/mc23_13p6TeV.601229.PhPy8EG_A14_ttbar_hdamp258p75_SingleLep.deriv.DAOD_PHYSLITE.e8514_e8528_s4162_s4114_r15540_r15516_p7266/DAOD_PHYSLITE.49568713._001693.pool.root.1
