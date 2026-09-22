export function pluginRunning(plugins, name) {
  return plugins.some((plugin) => plugin.name === name && plugin.running);
}
