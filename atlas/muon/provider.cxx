#include "correction_plugin.h"
#include "efficiency/efficiency.h"
#include "selection/selection.h"

#ifdef ATLAS_MUON_WITH_MOMENTUM
#include "momentum/momentum.h"
#endif

extern "C" int correctionlib_plugin_api_version() {
    return correction::plugin_api_version;
}

extern "C" const char* correctionlib_plugin_build_id() {
    return correction::plugin_build_id().c_str();
}

extern "C" void correctionlib_register_nodes_v4(correction::PluginRegistry& registry) {
#ifdef ATLAS_MUON_WITH_MOMENTUM
    register_momentum(registry);
#endif
    register_efficiency(registry);
    register_selection(registry);
}
