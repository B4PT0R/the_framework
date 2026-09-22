# Framework model/style audit

Scope: all Python modules in `the_framework.agent`, `server`, and general plugins.
Baseline: `d9bd37c`. No deployment or restart is part of this pass.

Final verification: `pytest -q tests/application tests/agentic` — **875 passed
in 92.25 seconds**. Targeted Ruff checks and `git diff --check` pass. Both direct
constructors and dynamic factories, examples and installed-wheel consumers use
the named-field convention. Regression tests cover runtime identity, frozen
declarations, independent defaults and private browser attributes.

## Inventory and investigation order

- Pure transport/state values: PluginStatus, SurfaceRelease, CapabilityLease,
  PendingPairing, Principal. Prefer the model itself as the payload; distinguish
  private connection identifiers/paths from public data.
- Declarations: SessionPolicy, QueuePolicy, AgentSpec, BuildContext, Capability,
  CapabilityRequirement, Extension, Plugin, ApplicationPlan, AgentApplication,
  ServiceSpec, ClientSurface, WebSocketEndpoint. Inspect frozen semantics,
  validation ordering, positional consumers and callable identity before migration.
- Runtime holders: PendingSteering, BrowserSession, ClientApplicationConnection,
  EndpointContext. Separate runtime attributes from data where meaningful; do not
  serialize futures, requests, sockets, browser handles or queues accidentally.
- Existing modict models: inspect manual reconstruction, repeated DTO conversion,
  computed state, field/model validation and homogeneous registries throughout the
  three subpackages. This review is not limited to dataclasses.

Canonical modict README and tests confirm `frozen`, `strict`, `extra`, JSON
enforcement, factories and non-payload attributes. Model validators run against
the live mapping; frozen validation must not mutate it. Assess these semantics
per model, not via mechanical decorator replacement.

## Completed

- PluginStatus is a strict frozen JSON model; HTTP and remote handlers return it
  directly, eliminating seven-field copying and the internal `.payload()` wrapper.
  Plugin-host/framework tests pass; direct serialization and mutation checks added.
- SessionPolicy and QueuePolicy are frozen JSON models. Literal validation owns
  mode checks; the queue model validator owns bounds. Constructors now use named
  fields (internal positional consumers migrated); invalid modes raise the native
  modict TypeError. Roundtrip and immutability regression tests cover both.
- Native nested coercion replaces manual reconstruction for WorkerReady specialist
  profiles, WorkerStatus usage windows and function parameter properties. Tested
  directly: modict reconstructs AgentProfile and Param, but not a discriminated
  ResponseItem subclass. Polymorphic protocol conversions remain intentional;
  they require explicit discriminant handling rather than blind removal.
- SurfaceRelease is the frozen public payload (`surface`, `release`, `previous`).
  Removed duplicate id naming, payload copying, and an unused local path; owners
  resolve paths through SurfaceService. Build/publish/rollback wire shape retained.
- Capability and CapabilityRequirement use strict frozen JSON models and native
  model validation. BuildContext keeps its initial homogeneous mapping frozen
  with auto-conversion disabled, so runtime objects and nested dictionaries
  retain identity. Its separate construction-time exports and contribution
  channels let ordered plugin factories cooperate without changing those
  initial inputs; `require` still reports missing values explicitly.
- ResponseUsage delegates nested TokenDetails reconstruction to its typed fields;
  removed the private helper and manual payload rebuild. SDK to_dict/model_dump
  adapters and identity-preserving recasts remain covered.
- CommandEvent and response-item added/done events now reconstruct polymorphic
  fields with modict validators. Direct construction and wire decoding share the
  same semantics without changing caller dictionaries; already-typed objects keep
  identity.
- CommandAccepted/Completed/Failed and PromptRequest use field validators for
  nested request/item reconstruction. Direct and wire construction preserve
  polymorphism without altering input dictionaries or replacing live requests.
- SessionSnapshot/SessionTurn reconstruct response items with field validators;
  SessionPage delegates watermark and nested turns to native coercion. Tests check
  raw container element types as well as equality to detect lazy conversion side
  effects. Field validators avoid reading shared lists through model auto-conversion;
  decorated validators are registry entries, not callable instance methods.
- RemoteClientPolicy is a strict frozen modict declaration. Set operations and
  capability-graph validation remain unchanged; JSON enforcement is deliberately
  off because frozensets are runtime policy data, not a wire document. Unknown
  keys and invalid field types are rejected before the policy reaches transport.
- Principal is a frozen strict modict identity with independent attribute defaults;
  all local consumers use named fields. Scopes remain frozensets, and endpoint
  execution context/request objects are not included in the identity payload.
- WorkerStatus, ApplicationCall and AgentCompactionEnd now use field-level
  reconstruction too. Regression checks preserve active request identity and
  retained source metadata when decoding retired compaction items. Protocol and
  local-event modules no longer implement mutating from_dict overrides.

## Other inspected areas

- General-plugin configurations already derive from modict Config: memory,
  registry, realtime, scheduler, browser, bash, system and web search. Bounds,
  uniqueness and explicit bool rejection in numeric settings protect domain rules,
  not merely Python annotations; do not delete them as redundant typing checks.
- Tools, Providers, Hooks, Instructions and Endpoints already use homogeneous
  modict registries and payload/runtime separation. No parallel model layer is
  needed for these collections.
- Embedding normalization is numerical sequence processing with dimension and
  finite-norm checks. Wrapping vectors in a mapping would not simplify it.
- Endpoint schema adaptation crosses an external schema/JSON Schema boundary;
  it is not equivalent to the removed seven-field payload-copying wrappers.

## Complete model harmonization

The initial nine migrations are now followed by all fourteen remaining core
dataclasses. No dataclass remains in `the_framework`. Keeping a second convention
merely because a record is not JSON added cognitive cost without a demonstrated
technical benefit.

- AgentSpec, Extension, Plugin, AgentApplication, ApplicationPlan, ServiceSpec,
  WebSocketEndpoint, EndpointContext and ClientSurface are frozen strict modict
  declarations. Their non-JSON object references remain unchanged; normalization
  uses native validators instead of bypassing frozen setters.
- PendingSteering, PendingPairing, CapabilityLease and ClientApplicationConnection
  are mutable typed runtime records. The product's connection subclass follows
  the same convention. Lease public projection still excludes connection identity.
- BrowserSession stores its private Playwright handles and cleanup tasks in attrs
  outside the mapping; factories keep mutable configuration defaults independent.
- Mapping does not mean public JSON: runtime declarations keep JSON enforcement
  off and lazy nested conversion disabled. Only explicit protocol projections
  cross serialization boundaries.
- Constructors use named fields. Extension's `factory` is now `service_factory`
  to avoid colliding with modict's factory primitive. Dependent changes rebuild
  with `Model({**previous, ...changes})` so validation sees the complete new state,
  rather than intermediate assignments from `|`/`update`.

The migration exposed two general modict defects, fixed upstream in local commit
`18c6160` (0.4.19): postponed annotations now resolve in the declaring module,
and callable checks accept compatible variadic/defaulted signatures. Its 591-test
suite passes. Version 0.4.19 was pushed and published to PyPI at the user's
request. The Harness now requires 0.4.19 and uses the published wheel, not an
editable installation. Published artifact hashes match the tested source build.

## Scope and preserved boundaries

- Agent: configuration, declaration, typed protocol/events, response/usage models,
  registry composition and runtime-state holders were inspected. Reconstruction
  now belongs to typed fields where applicable; SDK adapters and discriminant
  interpretation remain explicit. No concurrent session writer was introduced.
- Server: compilation/lifecycle, HTTP/WebSocket identity, surface results and
  remote policy/state were examined. Public JSON shapes remain unchanged; internal
  Python callers migrated to named fields where their model changed.
- General plugins: configuration and registry model patterns were reviewed across
  all plugin families listed above. Storage, numerical embeddings and execution
  handles remain with their owners, rather than gaining speculative DTO layers.
- Shared modict fixes are generic and tested independently; no application policy
  was introduced into the library.
- No frontend, device protocol, inference vendor payload or durable storage format
  was intentionally changed. Physical-device/UI acceptance is not claimed by this
  Python-only pass. No Harness service restart, deployment or push was performed.
