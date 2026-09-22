# Changelog

Notable changes to the Python distribution are recorded here. Versions follow
the distribution `b4pt0r-the-framework`; the import remains `the_framework`.
Until a stable release, minor versions may revise the public composition API.

## Unreleased

- Make `Plugin` the single declaration for a modular feature's agent binding,
  server runtime, private agents, public capabilities and dependencies. Server
  routes and services stay installed until restart; only agent bindings change
  live.
- Accept a standalone server service as a plugin runtime, while retaining
  `Extension` for multi-service lifecycles, explicit routes and dependency
  injection. Validate versioned cross-plugin service dependencies.
- Keep the starter's chat and voice features independently installable, and
  expose server-only plugins distinctly from agent bindings.
- Serialize concurrent agent-binding changes and keep the starter scheduler's
  server service running when its agent binding is hidden.
- Preserve the supervised worker, session, HTTP/WebSocket and editable surface
  contracts while simplifying application assembly. This release revises the
  public composition API from 0.1.0.

For applications upgrading from 0.1.0, replace `PluginSpec` with `Plugin` and
put the agent class in `agent=`. Remove `runtime_enabled` and
`runtime_required`: an installed plugin now keeps its server runtime until the
application is restarted. To remove that runtime, remove the plugin from
`AgentApplication.plugins` and restart; to hide only its tools and context from
the agent, change its binding through the plugin host. A plugin with several
server services can return multiple `Extension` declarations from `runtime`.

## 0.1.0 - 2026-09-22

- Provide the `AgentApplication` composition API for one persistent primary
  agent, server extensions, plugins, private specialists and client surfaces.
- Include the editable starter application and `the-framework bootstrap` CLI.
- Package typed Python modules and runtime resources with MIT licensing.
