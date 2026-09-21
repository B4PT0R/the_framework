# Neutral local-agent starter — acceptance audit

## Scope and composition

The deliverable is `starter/`: a usable, editable **local desktop application**,
not an endpoint demo or a mandatory framework UI. It composes `the_framework`
without importing `the_harness`, reading product configuration or activating
hardware. Its data directory, instructions, UI and identity are independent.

- `application.py`: agent/resources, eight general plugins and client surface.
- `server.py`: extensions, canonical queue, attachments and plugin controls.
- `voice.py`: call-scoped voice lifecycle over the framework's RealtimeController.
- `desktop.py`: persistent Playwright/browser RPC and supervised server socket.
- `ui/src/`: chat/composer, separate settings component, display projection and
  voice lifecycle; shared WebRTC transport/transcript assembly are reused.
- `instructions.md` and `memory.md`: editable neutral application policy.
- [Setup and extension guide](../starter/README.md).

The smallest useful public primitives were reused: AgentApplication, AgentSpec,
AgentResources, PluginSpec, Extension, endpoint and ClientSurface. No alternate
session, memory format, browser sidecar or product-policy adapter was introduced.

## Requirement-by-requirement evidence

| Requirement | Implementation and authoritative verification |
| --- | --- |
| Neutral, independent assembly | `test_starter_assembles_with_product_imports_forbidden` builds with all product imports rejected; framework import/wheel tests independently check the distribution |
| Standalone bootstrap | `the-framework bootstrap` copies the wheel-bundled template into a separate code directory, records a private data directory, and refuses nonempty or overlapping targets; wheel-only and clean npm install/build tests verify extraction |
| Persistent chat and ordered streaming | CanonicalAgentApi + worker events; UI projection tests cover deltas, duplicate events, stale snapshots, chronological pages; real tool/inference test checks canonical reply |
| Tool activity and interruption | Tool feedback events displayed in the composer; actual Bash roundtrip emits tool start/end; authenticated interrupt submission and framework worker cancellation tests |
| History and recovery | Real worker recreation restores configuration/transcripts; real Chromium retains its window and conversation through supervised server replacement |
| Attachments | Core atomic upload helper; files copied before queue submission, image validation/vision payload, collision and invalid-image tests; Chromium stages/removes attachments |
| Model, configuration and plugins | Dedicated settings component; real config save/reload, invalid JSON handling, plugin activation persisted across worker recreation; connection/working state shown in UI |
| All general plugins including memory | Composition test enumerates Bash, registry, web search, memory, scheduler, system, realtime and browser; Jiminy discovered as private specialist |
| Functional memory | Opt-in real primary request → Jiminy process → durable SQLite entry → embeddings → semantic retrieval after reconstructing the agent |
| Functional voice | Opt-in actual WebRTC/backend exchange with simulated microphone, inbound audio, live caption, canonical transcript and peer cleanup; deterministic ownership/abort/disconnect tests |
| Optional credentials/services | Real worker fixture forbids interactive authentication; config/history/status remain available after failed inference; lazy client retries authentication after a prior failure |
| Editable own application | Neutral instructions describe inspection, controlled changes, verification, preview/publish and supervised restart; no fixed product identity or content |
| UI self-modification loop | Real Chromium edits an isolated source copy, builds, previews separately, publishes and rolls back; main conversation survives |
| Browser ownership and shutdown | Parent retains socket/browser across restart; authenticated cookies installed lazily; shutdown cancels startup; subprocess tests verify cleanup |
| Local security/privacy | Exact Host/Origin and bearer/cookie tests; socket origin checks; credentials outside renderer storage/URLs; isolated test directories |
| Concise, pedagogical surface | One short composition, capability-owned modules, settings separated from chat, shared service/control/upload reuse; no forwarding-only compatibility layers |
| Documentation and handoff | Starter README covers installation, explicit login, data layout, settings, extensions, self-modification, tests and external boundaries; root and framework guides link to it |
| Preserve live product and commits | Work/test instances are isolated; no product restart or personal-data mutation; changes committed in coherent local increments, not pushed |

## Generic corrections established by this application

- Scheduler/system service implementations and application controls are reusable
  framework capabilities, consumed by both product and starter.
- Attachment copying is atomic and collision-safe at the shared storage boundary.
- Browser startup handles headless app-mode and Playwright callback ownership;
  authentication cookies do not eagerly start the secondary browsing context.
- Application-referenced specialists receive their **resolved runtime profile** as
  well as executable composition. Otherwise memory silently used an ephemeral
  default store despite successful curation.
- Backend authentication is lazy and non-interactive in the agent; quota refresh
  does not resolve authentication on the asyncio loop.
- WebRTC browser transport is shared with mobile; call correlation prevents
  failed previews from stopping another window's active voice call.

## Verification checkpoint

Final full Python run: **932 passed, 4 opt-in tests skipped** (116.88s).
This includes credential-free startup, lazy authentication, plugin persistence
and explicit product-import exclusion. The **3 real backend tests passed**
separately (68.48s): text/tools, memory, and WebRTC audio/transcription/cleanup.
Starter frontend: 13 tests; remote-client: 44 tests; mobile: 27 tests.
Starter build and remote-client/mobile typechecks pass. Real Chromium recovery
and edit/preview/publish/rollback passed after the final UI module split.

Live tests deliberately require `STARTER_LIVE_TESTS=1` because they spend backend
quota. Browser recovery requires an explicit `PLAYWRIGHT_BROWSERS_PATH`.
Neither is a substitute for tests with physical audio devices on the user's OS.

## Explicit boundaries, not hidden promises

- Local-only starter: no pairing, mobile/Quest shell or public deployment is
  included. The framework retains its distributed-client capabilities.
- The starter is bundled as a bootstrap template in the framework wheel, but has
  no separately published distribution. Its browser build has no product package
  dependency and the generated application runs outside the checkout.
- Actual text, tools, memory and WebRTC have been exercised using the current
  account. Availability, quota, network and future backend changes remain external.
- Audio uses a simulated microphone in automation; no claim is made about physical
  microphone quality, permissions or latency on untested machines.
- Agent Bash has the user's OS permissions; this is an editable local harness,
  not a sandbox. No personal product data was used in acceptance tests.
