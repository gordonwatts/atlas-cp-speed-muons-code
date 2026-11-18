cmake_minimum_required(VERSION 3.20)
project(atlas_muon_selection_efficiency_probe LANGUAGES CXX)
find_package(correctionlib CONFIG REQUIRED)

set(ATLAS_MUON_SELECTION_SOURCE "" CACHE PATH "Directory containing the reviewed selection source")
set(ATLAS_MUON_EFFICIENCY_SOURCE "" CACHE PATH "Directory containing the reviewed efficiency source")
add_library(atlas_muon MODULE partial_provider.cxx
    "${ATLAS_MUON_SELECTION_SOURCE}/selection.cc"
    "${ATLAS_MUON_EFFICIENCY_SOURCE}/efficiency.cc"
)
target_compile_features(atlas_muon PRIVATE cxx_std_20)
target_include_directories(atlas_muon PRIVATE "${ATLAS_MUON_SELECTION_SOURCE}" "${ATLAS_MUON_EFFICIENCY_SOURCE}")
target_link_libraries(atlas_muon PRIVATE correctionlib)
set_target_properties(atlas_muon PROPERTIES
    CXX_EXTENSIONS OFF
    BUILD_WITH_INSTALL_RPATH ON
    INSTALL_RPATH "$ORIGIN/../../correctionlib/lib"
)
