#include M05_SOURCE

#include <iostream>
#include <thread>

namespace {

using namespace atlas::momentum;

void require(bool condition, const char* scenario) {
    if (!condition) throw std::runtime_error(scenario);
}

double perturb(double value, std::uint64_t mask) {
    return std::bit_cast<double>(std::bit_cast<std::uint64_t>(value) ^ mask);
}

void expect(const char* scenario, const Covariance& id, const Covariance& me,
            CovarianceObservation expected, std::uint8_t phase = 0, int collision = 0, int eviction = 0) {
    const auto before = covariance_counts;
    StatCombinationWeights source;
    const Status status = prepare_stat_combination(id, me, source);
    PreparedStatCombination local;
    const auto& result = cached_stat_combination(id, me, phase, local);
    require(result.status == status, scenario);
    for (std::size_t p = 0; p < before.size(); ++p) {
        for (std::size_t counter = 0; counter < covariance_observations; ++counter) {
            const auto expected_delta = p == phase
                ? (counter == expected) + collision * (counter == covariance_hash_collision)
                    + eviction * (counter == covariance_eviction)
                : 0;
            require(covariance_counts[p][counter] - before[p][counter] == expected_delta, scenario);
        }
    }
    if (status == ok) {
        for (const auto member : {&StatCombinationWeights::id, &StatCombinationWeights::me,
                                  &StatCombinationWeights::combined, &StatCombinationWeights::inverse_sum}) {
            const auto& baseline = source.*member;
            const auto& observed = result.weights.*member;
            for (std::size_t j = 0; j < covariance_coefficients; ++j) {
                require(std::bit_cast<std::uint64_t>(observed.data()[j]) ==
                        std::bit_cast<std::uint64_t>(baseline.data()[j]), scenario);
            }
        }
    }
}

} // namespace

int main() {
    require(standard_covariance_fp_control(), "fixture expects the production FP control");
    const Covariance unit = Covariance::Identity(), doubled = 2. * unit;
    CovarianceKey reference;
    require(covariance_key(unit, unit, reference), "same-reference key");
    for (std::size_t detector = 0; detector < 2; ++detector) {
        for (std::size_t field = 0; field < covariance_coefficients; ++field) {
            Covariance id = unit, me = unit;
            auto& changed = detector == 0 ? id : me;
            changed.data()[field] = perturb(changed.data()[field], 1);
            CovarianceKey key;
            require(covariance_key(id, me, key), "exact finite coefficient");
            const auto index = detector * covariance_coefficients + field;
            for (std::size_t i = 0; i < key.size(); ++i) {
                require((key[i] ^ reference[i]) == (i == index ? 1 : 0), "one-bit coefficient boundary");
            }
        }
    }
    expect("first same-reference pair", unit, unit, covariance_miss_ok);
    expect("repeat same-reference pair", unit, unit, covariance_hit_ok);
    Covariance one_bit = unit;
    one_bit(0, 0) = perturb(one_bit(0, 0), 1);
    expect("one-bit miss", one_bit, unit, covariance_miss_ok);
    expect("one-bit old key valid", unit, unit, covariance_hit_ok);
    expect("order: ID then ME", unit, doubled, covariance_miss_ok);
    expect("order: ME then ID", doubled, unit, covariance_miss_ok);
    expect("order remains distinct", unit, doubled, covariance_hit_ok);
    expect("separate nominal/shifted bank", unit, doubled, covariance_miss_ok, 1);
    expect("repeat shifted bank", unit, doubled, covariance_hit_ok, 1);
    Covariance signed_zero = unit;
    signed_zero(0, 1) = signed_zero(1, 0) = -0.;
    expect("signed zero miss", signed_zero, unit, covariance_miss_ok);
    expect("signed zero sorted hit", signed_zero, unit, covariance_hit_ok);
    Covariance singular = unit;
    singular(0, 0) = 0.;
    expect("singular miss", singular, unit, covariance_miss_error);
    expect("singular hit", singular, unit, covariance_hit_error);
    Covariance almost_singular = singular;
    almost_singular(0, 0) = 0x1p-20;
    expect("near singular full-rank miss", almost_singular, unit, covariance_miss_ok);
    expect("near singular full-rank hit", almost_singular, unit, covariance_hit_ok);
    Covariance exceptional = unit;
    exceptional(0, 0) = std::bit_cast<double>(0xfff8000000001234ULL);
    expect("signed NaN bypass", exceptional, unit, covariance_nonfinite_input);
    expect("NaN never cached", exceptional, unit, covariance_nonfinite_input);
    exceptional(0, 0) = std::numeric_limits<double>::infinity();
    expect("infinity bypass", unit, exceptional, covariance_nonfinite_input);

#if defined(__x86_64__) && defined(__SSE2__)
    const auto fp_control = _mm_getcsr();
    _mm_setcsr(fp_control | 0x2000u);
    expect("changed rounding bypass", unit, unit, covariance_fp_bypass);
    _mm_setcsr(fp_control);
    _mm_setcsr(fp_control | 0x8000u);
    expect("FTZ bypass", unit, unit, covariance_fp_bypass);
    _mm_setcsr(fp_control);
#endif

    // Construct distinct finite keys with exactly the same 64-bit hash.
    atlas_muon_m05_covariance_reset();
    Covariance same_hash = unit;
    same_hash.data()[0] = perturb(same_hash.data()[0], 1ULL << 10);
    same_hash.data()[6] = perturb(same_hash.data()[6], 1);
    CovarianceKey collided;
    require(covariance_key(same_hash, unit, collided) && collided != reference &&
            covariance_hash(collided, 0) == covariance_hash(reference, 0), "same-hash fixture");
    expect("seed collision", unit, unit, covariance_miss_ok);
    expect("hash match with a different key", same_hash, unit, covariance_miss_ok, 0, 1);
    expect("original collision key", unit, unit, covariance_hit_ok);
    expect("second collision key", same_hash, unit, covariance_hit_ok, 0, 1);

    // Force a FIFO replacement with five different matrices in one four-way set.
    atlas_muon_m05_covariance_reset();
    std::array<std::vector<std::uint64_t>, covariance_cache_sets> buckets;
    std::vector<std::uint64_t> same_set;
    for (std::uint64_t mask = 1; mask <= covariance_cache_sets * covariance_cache_ways + 1; ++mask) {
        one_bit(0, 0) = perturb(1., mask);
        CovarianceKey key;
        require(covariance_key(one_bit, unit, key), "replacement key");
        auto& bucket = buckets[covariance_hash(key, 0) % covariance_cache_sets];
        bucket.push_back(mask);
        if (bucket.size() > covariance_cache_ways) {
            same_set = bucket;
            break;
        }
    }
    require(same_set.size() == covariance_cache_ways + 1, "one over set capacity");
    for (std::size_t n = 0; n < same_set.size(); ++n) {
        one_bit(0, 0) = perturb(1., same_set[n]);
        expect("same-set cold miss", one_bit, unit, covariance_miss_ok, 0, 0, n == covariance_cache_ways);
    }
    one_bit(0, 0) = perturb(1., same_set.front());
    expect("evicted key recomputed", one_bit, unit, covariance_miss_ok, 0, 0, 1);
    expect("reinserted key hits", one_bit, unit, covariance_hit_ok);
    std::thread worker([&] { expect("different thread starts cold", one_bit, unit, covariance_miss_ok); });
    worker.join();
    expect("main thread still holds its key", one_bit, unit, covariance_hit_ok);
    atlas_muon_m05_covariance_reset();
    expect("explicit reset expires every old key", one_bit, unit, covariance_miss_ok);

    std::size_t layout[4]{};
    atlas_muon_m05_covariance_layout(layout);
    std::cout << "key, status, collision, replacement, FP and thread fixtures passed\n";
    std::cout << layout[0] << " table bytes; " << layout[1] << " sets; " << layout[2] << " ways; "
              << layout[3] << " diagnostic counter bytes\n";
}
