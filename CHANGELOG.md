# Changelog

Notable changes to the Python distribution are recorded here. Versions follow
the distribution `b4pt0r-the-framework`; the import remains `the_framework`.
Until a stable release, minor versions may revise the public composition API.

## 0.3.1 - 2026-10-28

- Add opt-in immutable Docker backend builds, isolated validation, release
  selection and host supervision, with explicit application mounts and timezone.
  Keep production credentials outside images and validate runtime inputs before
  calling the engine.
- Add explicit local/host process execution with authenticated host transport and
  application-owned worker recipes. The deployment CLI accepts an opt-in
  `execution_factory` and owns its resources through failure and shutdown.
- Reuse private atomic persistence for deployment settings and release snapshots;
  preserve the committed state when a subsequent directory sync fails.
- Use `open_application_ui` consistently on Unix and host browser RPC transports.
  Remove the former `open_harness_ui` wire alias: upgrade persistent browser
  daemons together with their callers. The saved `harness_ui` profile is unchanged.
- Reject duplicate Host/Origin headers in the starter's local authentication.
  Clarify the 0.3 composition API and deployment/execution ownership contracts.

- Pin Playwright 1.63.0 to keep application browser runtimes aligned. Isolate
  test cache selection from running applications, validate the matching Chromium,
  and wait for actual window focus before native close-key tests.

## 0.3.0 - 2026-10-27

- Require Modict 0.5.x and its `default_required` contract, plus SDK 0.5.9.
  Preserve explicit field requirement overrides and project matching tool schemas.
- Keep committed persistence snapshots coherent after post-commit failures, and
  own asynchronous event stream producers through loop shutdown.

- Bound declarative multipart streams before parsing with configurable
  `Application.max_upload_bytes` (64 MiB contents plus 1 MiB envelope by default),
  independent of Content-Length. Share the content limit with chat attachment
  copying, close partial and parsed spools on rejection/dispatch, and retain
  transactional cleanup without overwriting existing files.

- Route supervised restart recovery using the actual live voice owner at the
  completed-turn boundary, including restarts requested by autonomous goals.
  Correlate browser recovery across busy retries/reloads, defer goal continuations
  during recovery, and fall back to canonical text after 45 seconds without a
  bound voice delegation. Never reopen explicitly stopped or failed calls on reload.

- Relay backend compaction start/end and command failure to the live voice context,
  including while a delegation is running, without adding voice tools, forcing
  speech or persisting these operational notifications in canonical history.

- Give the agentic plugin text-context occupancy telemetry and an explicit
  `compact` tool. Report the latest local estimate without resampling providers;
  compact through the existing canonical mechanism before the next model step,
  after the complete tool batch, then continue the same turn. Document deliberate
  compaction at task transitions and preservation of necessary working state.

- Persist voice-call start/end developer markers and include them in voice
  context. Queue canonical items through ExternalEventRequest without starting
  inference, including after a plugin binding is disabled. Reserve initial
  context capacity and drain final markers after transport cleanup.

- Preserve delegated voice-response provenance in canonical messages and streamed
  text events through scoped `PromptRequest.response_kind`. Ordinary text turns
  keep their standard kind; clients can distinguish spoken and backend output
  consistently across streaming and session replay.

- Use low reasoning effort for delegated voice turns instead of forcing none,
  which models such as GPT-6.1 Sol reject. Keep the override scoped to the command;
  ordinary text conversations retain their configured reasoning settings.

- Add `artifacts.supported_artifacts`: an application-wide catalogue of kinds,
  input payload schemas and examples. Declare custom kinds with Artifact
  subclasses and typed modict payloads, deriving schemas from the same models
  that validate tool inputs. Keep their canonical conversation type as artifact.
  Recommend consulting the catalogue when uncertain, without per-client negotiation.

- Honor explicit `strict: false` function-tool metadata. Use it for
  `artifacts.show_artifact`, whose open JSON payload is incompatible with strict
  object schemas; preserve strict mode by default for other tools.

- Keep AgentSpec exclusively about reusable agent construction. AgentTrigger owns
  queue, idle shutdown, completion requirements and retries, and compiles the
  execution profile. Remove fleet_profile and recipe orchestration fields. A
  required completion tool supports a single attempt without forced retries.
- Give resource factories the actual session workspace directory, independent of
  persistence. Remove fabricated session filenames and the unused memory-path
  helper. Expose Session with the core root-level construction API.
- Remove the parallel internal application builder and unused extension-copy
  shortcut. Pass sessions explicitly to assembly; install authenticated routes
  and surfaces in a distinct internal phase without adding public concepts.
- Refresh the onboarding recipe, responsibility guide and historical audit labels.
  Keep memory implementation helpers private to the feature package.
- Preserve explicit private invocation policies in compilation and direct launches;
  adding a helper keeps class-discovered triggers. Named requests route to their
  target independently of ambient observation filters. Remove obsolete inline
  conversions of specialist result fields and pending-task documents.
- Update the starter source-map-js lock to 1.2.2, fixing [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q);
  npm audit reports no remaining vulnerabilities in the starter dependency tree.

- Remove session lifetime from AgentSpec. Construct it with a Session:
  `Session()` keeps context in memory, `Session(retain_history=False)` clears
  context between tasks, and `Session.open(path)` persists on disk. Retention
  is canonical session data and survives resume; paths remain runtime metadata.
  Triggers and WorkerProfile use independent `durable`/`retain_history` booleans,
  replacing the three-mode vocabulary. WorkerReady announces effective retention
  so fleet retries follow the resumed session rather than changed launch defaults.
  Starter and Harness recipes no longer declare a lifetime; hosted sessions remain
  durable by default. No compatibility aliases or runtime migrations are added.

- Replace `primary_agent` with the sole public `Application.agents` catalogue.
  One recipe is selected implicitly; several require `build(..., agent=...)` for
  a new default session. Resume the saved `Session.agent_name` without changing
  its identity. Keep private specialists separate from public agent recipes.
- Give every conversation the same session-scoped lifecycle, including the default
  session. Root conversation routes delegate to it; named session routes also
  address it. Closing/reopening it preserves durable state without stopping sibling
  workers or application-owned services. `default_session_id` identifies this role;
  `default_session` exposes its current controller. Session shutdown closes its
  event connections so clients can reconnect to the replacement controller.

- Default agent applications to an editable desktop `ui/` beside their explicit
  declaration module, with no ClientSurface wiring in the starter. Resolve the
  source at server build only; `surfaces=()` opts out and missing sources fail
  clearly. ClientSurface supplies standard desktop defaults so custom apps repeat
  only nonstandard settings; explicit multi-device declarations remain supported.

- Make plugin server assembly explicit: `server_extensions` contains component
  declarations and `configure(context)` collects advanced build-time contributions.
  Remove the overloaded `server_extension` field and implicit bare-service wrapping.
  Keep service factories separate and resolve lazy references before dependency
  ordering. Bundled plugins, examples, starter and Harness follow the same contract.
- Let session service contexts borrow application resources without taking over
  their lifecycle. Keep shared health/error reporting and live lookups, including
  views captured during configuration; session shutdown stops only local services.
- Check the synchronized Python environment and a real headless Chromium launch
  before preparing a starter again. A ready run skips installations and npm rebuilds;
  explicit `prepare` refreshes Python/Chromium, and missing resources are repaired.
- Expose declaration decorators together at the package root. Name the standard
  chat-plus-tool-calls projection `activity` rather than `remote`; client device
  type and authentication policy remain separate from history presentation.

- Remove application-specific Portrait and MediaReference payloads and their
  session projection exceptions. Generic Image inputs obey the same image limit,
  including dynamic context prefixes; Artifact remains the presentation primitive.

- Keep the starter following newly loaded images when layout delivers a scroll
  before the image load event. Leave follow mode on upward movement rather than
  treating content growth as a request to read older history.

- Recreate active voice calls after agent-binding changes, using an owning-client
  reconnect signal after canonical voice turns are reconciled. Manual stop wins;
  disabling voice does not reconnect. Gate memory/registry live producers by
  selected providers and clean their pending context on call replacement. Move
  accepted transcription commits into the canonical worker so disabling the
  realtime binding cannot discard final queued words.

- Make `the-framework bootstrap` prepare an independent app environment, Chromium
  and the editable UI by default, with `--run` for immediate launch and
  `--no-prepare` for source only. Add resumable `prepare` and `run` commands;
  checkpoint npm stages and rebuild when sources change. Diagnose missing uv/
  Node/npm before creation. Generated apps pin the framework and explicitly link
  its source checkout when bootstrapped during development.

- Unify client-visible files under `data_root/bucket/`: chat uploads and artifact
  publications share this tree, with authenticated, range-capable
  `/api/v1/bucket/{path:path}` delivery for any file type and nested paths. Remove
  the separate attachment-directory option and artifact-only file route. Earlier
  experimental storage layouts are not migrated.

- Add native `Artifact` outputs: tools and commands persist them in the canonical
  session; providers expose replaceable current-turn views without persistence.
  Project artifacts as developer notices with JSON, separately from vision-only
  `Image` inputs. Add the complete `ARTIFACTS` plugin for durable local assets and
  authenticated delivery. Starter and Harness render images/files and support
  application renderers with fallbacks for unknown kinds and rendering failures.

- Add `ApplicationSpec.build_conversation(reference, context, ...)` as the standard
  assembly path for the primary supervisor, runtime, authenticated API, health
  and transports. Migrate the starter and Harness; remove the former public
  `application_server_extensions` helper. Runtime hooks now own supervised restart
  and surface notifications instead of repeating their wiring in applications.
- Keep `BuildContext` inputs fixed and group plugin construction options under
  `plugins`. Remove `provide`; use declared service dependencies and the read-only
  `context.services` view of running services. Seal contributions before service
  construction and require a fresh context per build. Realtime declares its
  binding coordinator during contribution collection rather than mutating inputs
  while building its service.

- Release the Playwright driver after failed or cancelled persistent-browser
  startup, including cookie initialization failures. Serialize session startup
  and shutdown, and ignore delayed close events from an older context. Repeated
  attempts can recover without accumulating drivers and exhausting file handles.

- Expose `CanonicalTransportSockets` as the `canonical_transport` service, with
  `event_connection_count` for accepted canonical event sockets. Keep this
  connectivity distinct from internal consumers, capability bridges and paired
  clients; release it on disconnect, stream failure and cancellation.

- Integrate the host-owned chat service into `build_conversation()`, removing
  the `CHAT` plugin and its synthetic capability requirement. Share
  `server.runtime.ingress.ChatIngress.submit(PromptRequest)` with client transports so route
  selection and acceptance use the same text/voice transition barrier. Preserve
  local prompt and attachment wire contracts; reject text-only turn semantics
  during voice instead of dropping them.

- Derive plugin `running` status from its actual server service lifecycle instead
  of reporting a constant success. Preserve installed routes after noncritical
  startup failure and keep agent binding independent. Store only binding booleans
  in the plugin host; runtime status comes from the existing service graph.
  Direct `PluginHost` construction now requires that graph and its service owners.

- Allow reset participants to supply only suspension or only resumption, retaining
  ordered recovery. Let the complete Realtime plugin close and reconcile its call
  before a session reset; clear API call identity on shutdown. Remove the system
  plugin's placeholder suspension callback.

- Move HTTP declarations to `server.api.declarations`; expose `Endpoint` and
  `endpoint` through `the_framework.server` and keep the root `endpoint` import.
  Remove the worker's unused endpoint registry and agent-extension HTTP collection.
  Discover decorated services directly, independently of agent activation.
  Migrate imports from `the_framework.agent` and remove `Endpoints`,
  `AgentExtension.endpoints()` and `endpoint_declarations()`; use server discovery
  for explicit endpoint sources. HTTP payloads, routes and security are unchanged.

- Honor `@provider(channels=...)` on direct registration as well as extension
  composition. Preserve explicit empty channels and registration overrides,
  preventing unintended context delivery to default text or realtime projections.

- Name shared application controllers and client transports around `runtime`.
  Browser UI operations use `open_application_ui` and `application_ui_origins`,
  and open the application-selected route rather than imposing Harness paths.
  Preserve browser profiles, tool targets and RPC commands across upgrades.

- Apply endpoint signature/docstring inference when no explicit HTTP schema is
  supplied. Validate typed JSON inputs, coerce path/query values, preserve
  Python defaults and expose inferred contracts in OpenAPI. Remove redundant
  argument-free request schemas from framework and Harness services.
- Use `Plugin.name` consistently for its agent binding, configuration, state,
  tool namespace and private specialists. Previously a differently named
  extension could bypass the server's binding override. Keep standalone agent
  extension identities and existing matching plugin/session names unchanged.
- Keep the compiled application plan independent of server resolution. Reuse
  the ordered server registry directly instead of rebuilding the plan and its
  duplicate server indexes; installed registries are immutable.
- Derive service injection dependencies from factory or constructor parameters:
  required parameters require services, defaults make integrations optional.
  Keep explicit dependencies for extra ordering constraints and keyword adapters.
  Simplify bundled, starter and Harness server declarations accordingly.
- Execute resolved `ServerExtension` declarations directly for service lifecycle,
  removing the duplicate internal `ServiceSpec` model. Preserve capability and
  service dependencies, startup rollback and health diagnostics.
- Export complete bundled plugin declarations; scheduler and system own their
  server integrations and reset hooks. Move the neutral starter realtime API
  into its framework plugin. Server modules stay deferred during worker compile.
- Migrate starter and Harness to complete plugin imports, preserving persisted
  plugin identities, service state paths and realtime HTTP routes.

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
