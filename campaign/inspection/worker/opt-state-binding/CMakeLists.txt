cmake_minimum_required(VERSION 3.20)
project(muon_m12_named_map_replay LANGUAGES CXX)

find_package(correctionlib CONFIG REQUIRED)
add_executable(m12_named_map_replay named_map_replay.cxx "${MOMENTUM_SOURCE}/momentum_payload.cxx")
target_compile_features(m12_named_map_replay PRIVATE cxx_std_20)
target_include_directories(m12_named_map_replay PRIVATE "${MOMENTUM_SOURCE}" "${MUON_RAPIDJSON_INCLUDE_DIR}")
target_link_libraries(m12_named_map_replay PRIVATE correctionlib)
