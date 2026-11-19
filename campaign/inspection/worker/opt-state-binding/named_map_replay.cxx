#include "correction_plugin.h"
#include "momentum_payload.h"

#include <rapidjson/document.h>
#include <rapidjson/istreamwrapper.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <array>
#include <bit>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <time.h>
#include <unordered_set>
#include <vector>

namespace {
using atlas::momentum::Payload;
using atlas::momentum::State;

constexpr std::size_t expected_states = 29;
constexpr std::size_t expected_chunks = 10;
constexpr std::size_t expected_rows = 434913;
constexpr std::size_t repetitions_per_pair = 32;

void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}

Payload load(const char* path) {
    std::ifstream input(path, std::ios::binary);
    require(static_cast<bool>(input), "cannot open the accepted payload");
    rapidjson::IStreamWrapper stream(input);
    rapidjson::Document definitions;
    definitions.ParseStream(stream);
    require(!definitions.HasParseError() && definitions.IsObject() && definitions.HasMember("corrections") &&
            definitions["corrections"].IsArray(), "invalid accepted CorrectionSet JSON");
    const rapidjson::Value* config = nullptr;
    for (const auto& item : definitions["corrections"].GetArray()) {
        if (std::string_view(item["name"].GetString()) != "muon_momentum") continue;
        require(!config, "duplicate muon_momentum definition");
        config = &item["data"]["config"];
    }
    require(config && config->IsObject(), "missing native muon_momentum config");
    rapidjson::StringBuffer json;
    rapidjson::Writer<rapidjson::StringBuffer> writer(json);
    require(config->Accept(writer), "cannot serialize the accepted momentum config");
    return Payload(correction::PluginConfig(std::string(json.GetString(), json.GetSize())));
}

struct Batch {
    std::size_t first;
    std::size_t last;
    std::size_t rows;
    std::string state;
};

std::vector<Batch> read_batches(const char* path, const Payload& payload) {
    std::ifstream source(path);
    require(static_cast<bool>(source), "cannot open the original-source batch fixture");
    std::vector<Batch> batches;
    Batch entry{};
    while (source >> entry.first >> entry.last >> entry.rows >> entry.state) batches.push_back(entry);
    require(source.eof() && batches.size() == expected_chunks * expected_states, "unexpected batch fixture shape");
    std::unordered_set<std::string> states;
    std::size_t total_rows = 0;
    for (std::size_t i = 0; i < batches.size(); ++i) {
        const auto chunk = i / expected_states;
        const auto route = i % expected_states;
        const auto& batch = batches[i];
        require(batch.first == chunk * 2000 && batch.last == batch.first + 2000 &&
                batch.rows == batches[chunk * expected_states].rows && batch.state == batches[route].state,
                "unexpected original-source batch or state order");
        if (chunk == 0) {
            require(states.insert(batch.state).second && payload.states.contains(batch.state),
                    "state sequence does not match the native payload");
        }
        total_rows += batch.rows;
    }
    require(total_rows == expected_rows && payload.states.size() == states.size() && batches[0].state == "NOSYS",
            "wrong full-file row count or state inventory");
    return batches;
}

struct Witness {
    std::uint64_t checksum = 0;
    std::uint64_t rows = 0;
    bool operator==(const Witness&) const = default;
};

template<bool bind>
void replay(const Payload& payload, std::span<const Batch> batches, std::size_t repetitions, Witness& witness) {
    for (std::size_t pass = 0; pass < repetitions; ++pass) {
        for (const auto& batch : batches) {
            if (batch.rows == 0) continue;
            const State* state = nullptr;
            if constexpr (bind) state = &payload.states.at(batch.state);
            for (std::size_t row = 0; row < batch.rows; ++row) {
                State current;
                if constexpr (bind) current = *state;
                else current = payload.states.at(batch.state);
                auto action = static_cast<std::uint64_t>(current.action);
                auto direction = static_cast<std::uint64_t>(current.direction + 1);
                // The same compiler barrier and sink in both arms keep every
                // lookup/use inside its intended loop without an extra call.
                asm volatile("" : "+r"(action), "+r"(direction) :: "memory");
                witness.checksum = std::rotl(witness.checksum, 5) ^ (action + (direction << 8));
                ++witness.rows;
            }
        }
    }
}

template<bool bind>
std::string unknown_error(const Payload& payload, std::span<const Batch> batches, Witness& witness) {
    try {
        replay<bind>(payload, batches, 1, witness);
    } catch (const std::out_of_range& err) {
        return err.what();
    }
    throw std::runtime_error("diagnostic accepted a nonempty unknown name");
}

void check_edges(const Payload& payload, const std::string& varied) {
    const std::array edge_batches{
        Batch{0, 0, 0, "UNKNOWN_M12"}, Batch{0, 0, 2, "NOSYS"}, Batch{0, 0, 1, varied},
        Batch{0, 0, 1, "UNKNOWN_M12"}, Batch{0, 0, 3, "NOSYS"},
    };
    Witness per_row, bound;
    replay<false>(payload, std::span(edge_batches).first(1), 1, per_row);
    replay<true>(payload, std::span(edge_batches).first(1), 1, bound);
    require(per_row == Witness{} && bound == Witness{}, "empty batch made progress");
    const auto error = unknown_error<false>(payload, edge_batches, per_row);
    require(error == unknown_error<true>(payload, edge_batches, bound) && per_row == bound && per_row.rows == 3,
            "state variant or failure order mismatch");
}

double cpu_seconds() {
    timespec ts{};
    require(clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts) == 0, "cannot read process CPU time");
    return static_cast<double>(ts.tv_sec) + static_cast<double>(ts.tv_nsec) * 1.e-9;
}

template<bool bind>
Witness timed(const Payload& payload, std::span<const Batch> batches, int pair) {
    Witness result;
    const auto wall_start = std::chrono::steady_clock::now();
    const auto cpu_start = cpu_seconds();
    replay<bind>(payload, batches, repetitions_per_pair, result);
    const auto cpu = cpu_seconds() - cpu_start;
    const auto wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - wall_start).count();
    require(result.rows == repetitions_per_pair * expected_rows && cpu > 0 && wall > 0,
            "timed replay row or clock mismatch");
    std::cout << pair << '\t' << (pair == 0) << '\t' << (bind ? "bound" : "named") << '\t'
              << repetitions_per_pair << '\t' << result.rows << '\t' << result.checksum << '\t'
              << wall << '\t' << cpu << '\n';
    return result;
}
}  // namespace

int main(int argc, char** argv) {
    require(argc == 3, "usage: m12_named_map_replay production-payload.json original-batches.tsv");
    const auto payload = load(argv[1]);
    const auto batches = read_batches(argv[2], payload);
    check_edges(payload, batches[1].state);
    std::cout << std::setprecision(17)
              << "pair\twarmup\tmode\trepetitions\trows\tchecksum\twall_seconds\tcpu_seconds\n";
    Witness expected;
    for (int pair = 0; pair <= 5; ++pair) {
        Witness per_row, bound;
        if (pair % 2 == 0) {
            per_row = timed<false>(payload, batches, pair);
            bound = timed<true>(payload, batches, pair);
        } else {
            bound = timed<true>(payload, batches, pair);
            per_row = timed<false>(payload, batches, pair);
        }
        if (pair == 0) expected = per_row;
        require(per_row == expected && bound == expected, "timed replay state sink mismatch");
    }
}
