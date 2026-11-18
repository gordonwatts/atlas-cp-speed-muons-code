#!/usr/bin/env bash
set -euo pipefail
result=${1:?checkpoint 3 path required}
sdk="$result/sdk-build-01/venv/lib/python3.14/site-packages/correctionlib"
echo "AL9 system compiler and runtime package licensing"
/usr/bin/g++ --version
rpm -q --qf '%{NAME} %{VERSION}-%{RELEASE} %{LICENSE}\n' gcc gcc-c++ libstdc++ libgcc glibc
for package in libstdc++ libgcc; do rpm -ql "$package" | rg '(/licenses/|COPYING|LICENSE)' || true; done
echo "System C++20 concepts/span compile probe"
/usr/bin/g++ -std=c++20 -fsyntax-only -x c++ - <<'CPP'
#include <concepts>
#include <span>
void use(std::integral auto value) { std::span span(&value, 1); }
int main() { use(42); }
CPP
echo "System C++20 format availability"
if /usr/bin/g++ -std=c++20 -fsyntax-only -x c++ - <<'CPP'
#include <format>
int main() { (void)std::format("{}", 42); }
CPP
then echo "std::format available"; else echo "std::format unavailable"; fi
for lib in "$sdk"/_core*.so "$sdk/lib/libcorrectionlib.so" \
    "$result/provider-build-02/build/libatlas_muon.so" "$result/portable-partial-03/build/libatlas_muon.so"; do
    echo "$lib"
    readelf -d "$lib" | rg '\(NEEDED\)|\(RUNPATH\)|\(RPATH\)'
    readelf --version-info "$lib" | awk '/Name: (GLIBCXX|CXXABI|GLIBC|GCC)_/ {print $3}' | sort -Vu
done
echo "Check selected GCC15 toolchain installation for runtime license files"
compiler_root=/cvmfs/sft.cern.ch/lcg/releases/gcc/15.2.0-35657/x86_64-el9
find "$compiler_root" -maxdepth 3 -type f \( -iname 'copying*' -o -iname '*license*' \) -print
sha256sum "$result/portable-partial-03/gnu-runtime/"*.so.*
