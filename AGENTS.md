# The Framework — Development Instructions

This repository owns the reusable `harness_core` Python package and the neutral
`starter` application. It must never import `the_harness` or assume Pandora's
identity, policies, personal data or device configuration. Product composition
belongs in `/home/baptiste/dev/the_harness` and depends inward on this package.

Keep one durable canonical conversational agent per application. Plugins own
their contributions and may declare private specialist agents; specialists do
not write canonical history. The server owns worker supervision, HTTP/WebSocket
transport, authentication and client surfaces. The browser owns its local media
and device runtime. Preserve persistence and protocol contracts when changing
these boundaries.

Use `modict` for structured models and inspect its API before adding conversion
layers. `/home/baptiste/dev/modict` and `/home/baptiste/dev/codex-backend-sdk` are
writable shared dependencies, but changes there must remain general and
backward-compatible. Do not introduce product-specific behavior into them.

The starter must remain an editable application with only framework dependencies.
Its bootstrap must work from the built wheel outside this checkout. Keep the
public framework API small; remove obsolete internal imports instead of adding
compatibility shims. Verify changes with focused tests, the full framework suite,
wheel isolation and, for UI changes, the starter's npm tests/build.

Create coherent local commits for verified changes. Do not publish or push
without an explicit request.
