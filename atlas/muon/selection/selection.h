#ifndef ATLAS_MUON_SELECTION_PROVIDER_H
#define ATLAS_MUON_SELECTION_PROVIDER_H

namespace correction {
class PluginRegistry;
}

void register_selection(correction::PluginRegistry& registry);

#endif
