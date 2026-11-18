#ifndef ATLAS_MUON_EFFICIENCY_PROVIDER_H
#define ATLAS_MUON_EFFICIENCY_PROVIDER_H

namespace correction {
class PluginRegistry;
}

void register_efficiency(correction::PluginRegistry& registry);

#endif
