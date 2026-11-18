#include "correction_plugin.h"
#include "efficiency.h"
#include "selection.h"

extern "C" int correctionlib_plugin_api_version() {
    static_assert(correction::plugin_api_version == 4);
    return correction::plugin_api_version;
}

extern "C" const char* correctionlib_plugin_build_id() {
    return correction::plugin_build_id().c_str();
}

extern "C" void correctionlib_register_nodes_v4(correction::PluginRegistry& registry) {
    register_selection(registry);
    register_efficiency(registry);
}
