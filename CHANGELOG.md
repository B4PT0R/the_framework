# Changelog

Notable changes to the Python distribution are recorded here. Versions follow
the distribution `b4pt0r-the-framework`; the import remains `the_framework`.
Until a stable release, minor versions may revise the public composition API.

## Unreleased

- Add canonical runtime configuration updates and durable event application,
  ordered remote-service listeners, and reusable filesystem reset staging.
- Provide explicit standard server declarations shared by starter and applications.
  Include runtime diagnostics in health and report failed live probes as degraded.

- Harmonize composition around `ApplicationSpec`, `Plugin`, `AgentExtension`
  and `ServerExtension`. Plugin contributions use `agent_extension`,
  `server_extension` and `specialists`; dependency fields distinguish public
  capabilities from local server services, and binding flags name the agent.
- Replace `SessionPolicy` with validated `AgentSpec.session_mode`, and rename
  agent-local plugin collections and methods to extensions. Remove the redundant
  extension `install()` shortcut. Name the worker's RPC proxy `ApplicationClient`.
- Keep agent and server implementations in plugin-owned `agent.py` / `server.py`
  modules. Migrate saved references to bundled agent extensions when reading
  specialist profiles, preserving persisted plugin identities and wire fields.
- Generalize endpoint source discovery and `register_endpoints` to ordinary
  services; agent extensions are no longer required for server API registration.

- Move the voice controller from `server.runtime.realtime` to
  `plugins.realtime.controller`. Import it from its plugin-owned module;
  the server and agent core remain usable without any bundled plugin.

- Build declared primary and private workers from `WorkerSupervisor` application
  inputs, replacing hand-written generic-worker commands and forwarding fleet
  factories in the starter and Harness.
- Expose realtime `append_context`, `discard_context` and `context_blocked`.
  Share ordered, latest-state and discard delivery policies behind voice
  barriers; remove redundant application delivery buffers. Report saturation
  rather than silently dropping accepted ordered context.

- Execute application calls independently of worker reception, with bounded
  concurrency, cancellation and cleanup across worker recovery.
- Keep receiving worker responses when an output observer fails, and roll back
  cancelled service startup without masking the original failure.
- Store build-context exports alongside inputs so all mapping operations agree.
- Apply plugin-binding controls while agent tools are suspended, preserving
  durable receipts and rejecting expired originating calls.
- Connect an installed primary runtime to plugin bindings and agent-facing
  controls automatically. Remove the former public `bind_application_controls`
  post-build step; pass an initial surface artifact as `BuildContext.ui_root`.
  Binding callback overrides accept `call=None` and forward it to
  `ApplicationRuntime.set_plugin_binding`.

For the next release, migrate `AgentApplication` to `ApplicationSpec`,
`AgentPlugin` and concrete `*Plugin` agent classes to `AgentExtension` and
`*Extension`, and `Extension` to `ServerExtension`. Replace `Plugin(agent=...,
runtime=..., agents=...)` with `Plugin(agent_extension=..., server_extension=...,
specialists=...)`. Plugin contract fields are `provides_capabilities`,
`requires_capabilities` and `optional_capabilities`; binding flags are
`agent_binding_enabled` and `agent_binding_required`. Server dependencies use
`requires_services` and `optional_services`. Application declarations use
`server_extensions` / `with_server_extensions`, while agent declarations and
instances use `extensions` / `add_extension`. Use `session_mode="durable"` rather
than `session=SessionPolicy.durable()`. Runtime import references move from
`plugins.<feature>.runtime:build_runtime` to
`plugins.<feature>.server:build_server_extensions`; agent implementations move
from `plugins.<feature>.plugin` to `plugins.<feature>.agent`. These are source API
changes without obsolete import aliases. Session documents, task envelopes,
HTTP/WebSocket payloads and their plugin identifiers retain their existing form.

## 0.2.1 - 2026-09-24

- Let an explicit YAML schema shape replace incompatible structure inferred
  from a Python annotation while retaining compatible metadata. Strict nested
  function-tool schemas no longer leak obsolete union branches.

## 0.2.0 - 2026-09-23

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
- Reconcile plugin bindings between the server and primary worker on startup,
  preserving existing session choices during migration and server choices after
  worker crashes or session reset. Enforce required bindings in the worker too.
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
