cmake_minimum_required(VERSION 3.20)
project(muon_m13_fixture LANGUAGES CXX)

find_package(correctionlib CONFIG REQUIRED)
add_executable(m13_fenv_probe probe_fenv.cxx)
target_compile_features(m13_fenv_probe PRIVATE cxx_std_20)
target_compile_definitions(m13_fenv_probe PRIVATE _GNU_SOURCE)
target_link_libraries(m13_fenv_probe PRIVATE correctionlib ${CMAKE_DL_LIBS})
