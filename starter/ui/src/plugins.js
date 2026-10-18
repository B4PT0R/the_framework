export function pluginBindingEnabled(plugins, name) {
  return plugins.some((plugin) => plugin.name === name && plugin.running && plugin.binding_enabled);
}
