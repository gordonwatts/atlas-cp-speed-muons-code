// Private to momentum.cxx. Included after prepare_stat_combination.
constexpr std::size_t covariance_coefficients = 25;
constexpr std::size_t covariance_cache_sets = 1024;
constexpr std::size_t covariance_cache_ways = 4;
constexpr std::uint8_t empty_covariance_phase = 255;
using CovarianceKey = std::array<std::uint64_t, 2 * covariance_coefficients>;

struct PreparedStatCombination {
    StatCombinationWeights weights;
    Status status = error;
};

struct CovarianceCacheEntry {
    CovarianceKey key;
    PreparedStatCombination prepared;
    std::uint64_t hash;
    std::uint8_t phase = empty_covariance_phase;
};

struct CovarianceCacheSet {
    std::array<CovarianceCacheEntry, covariance_cache_ways> entries;
    std::uint8_t next = 0;
};

thread_local std::array<CovarianceCacheSet, covariance_cache_sets> covariance_cache;

enum CovarianceObservation {
    covariance_hit_ok, covariance_hit_error, covariance_miss_ok, covariance_miss_error,
    covariance_eviction, covariance_hash_collision, covariance_nonfinite_input,
    covariance_nonfinite_weights, covariance_fp_bypass, covariance_observations
};

#ifdef ATLAS_MUON_M05_STATS
thread_local std::array<std::array<std::uint64_t, covariance_observations>, 2> covariance_counts{};
#endif

inline void observe_covariance([[maybe_unused]] std::uint8_t phase, [[maybe_unused]] CovarianceObservation event) {
#ifdef ATLAS_MUON_M05_STATS
    ++covariance_counts[phase][event];
#endif
}

bool finite_covariance_word(std::uint64_t bits) {
    constexpr std::uint64_t exponent = 0x7ff0000000000000ULL;
    return (bits & exponent) != exponent;
}

bool finite_covariance(const Covariance& matrix) {
    for (std::size_t i = 0; i < covariance_coefficients; ++i) {
        if (!finite_covariance_word(std::bit_cast<std::uint64_t>(matrix.data()[i]))) return false;
    }
    return true;
}

bool covariance_key(const Covariance& id, const Covariance& me, CovarianceKey& key) {
    // Store every coefficient in column-major order: first ID, then ME. Preserve signed zero.
    static_assert(Covariance::SizeAtCompileTime == covariance_coefficients);
    static_assert(std::numeric_limits<double>::is_iec559 && sizeof(double) == sizeof(std::uint64_t));
    const std::array matrices{&id, &me};
    for (std::size_t detector = 0; detector < matrices.size(); ++detector) {
        for (std::size_t i = 0; i < covariance_coefficients; ++i) {
            const auto word = std::bit_cast<std::uint64_t>(matrices[detector]->data()[i]);
            if (!finite_covariance_word(word)) return false;
            key[detector * covariance_coefficients + i] = word;
        }
    }
    return true;
}

std::uint64_t covariance_hash(const CovarianceKey& key, std::uint8_t phase) {
    std::uint64_t hash = 0x9e3779b97f4a7c15ULL + phase;
    for (const auto word : key) hash = std::rotl(hash, 9) ^ word;
    hash ^= hash >> 33;
    hash *= 0xff51afd7ed558ccdULL;
    return hash ^ (hash >> 33);
}

bool standard_covariance_fp_control() {
#if defined(__x86_64__) && defined(__SSE2__)
    // SSE2 binary64, nearest/even, masked traps, no flush-to-zero or denormals-are-zero.
    // Sticky flags are outside the correction contract and do not influence the matrix result.
    return (_mm_getcsr() & 0xffc0u) == 0x1f80u;
#else
    return false;
#endif
}

const PreparedStatCombination& cached_stat_combination(const Covariance& id, const Covariance& me,
                                                      std::uint8_t phase, PreparedStatCombination& local) {
    if (!standard_covariance_fp_control()) {
        observe_covariance(phase, covariance_fp_bypass);
        local.status = prepare_stat_combination(id, me, local.weights);
        return local;
    }
    CovarianceKey key;
    if (!covariance_key(id, me, key)) {
        observe_covariance(phase, covariance_nonfinite_input);
        local.status = prepare_stat_combination(id, me, local.weights);
        return local;
    }
    const auto hash = covariance_hash(key, phase);
    auto& set = covariance_cache[hash % covariance_cache_sets];
    for (const auto& entry : set.entries) {
        if (entry.phase != phase || entry.hash != hash) continue;
        if (entry.key == key) {
            observe_covariance(phase, entry.prepared.status == ok ? covariance_hit_ok : covariance_hit_error);
            return entry.prepared;
        }
        observe_covariance(phase, covariance_hash_collision);
    }

    local.status = prepare_stat_combination(id, me, local.weights);
    if (local.status == ok &&
        (!finite_covariance(local.weights.id) || !finite_covariance(local.weights.me) ||
         !finite_covariance(local.weights.combined) || !finite_covariance(local.weights.inverse_sum))) {
        observe_covariance(phase, covariance_nonfinite_weights);
        return local;
    }

    auto& entry = set.entries[set.next];
    set.next = (set.next + 1) % covariance_cache_ways;
    if (entry.phase != empty_covariance_phase) observe_covariance(phase, covariance_eviction);
    entry.key = key;
    entry.hash = hash;
    entry.prepared.status = local.status;
    if (local.status == ok) entry.prepared.weights = local.weights;
    entry.phase = phase;
    observe_covariance(phase, local.status == ok ? covariance_miss_ok : covariance_miss_error);
    return entry.prepared;
}
