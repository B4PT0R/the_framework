# The Framework — Development Instructions

This repository contains the reusable Python library used to build agent
applications (`core/`) and a small example application (`starter/`). The
separate `/home/baptiste/dev/the_harness` repository is one application built
with this library; its Pandora-specific behavior belongs there. Framework
code must never import that application or assume its identity, policies,
personal data or device configuration.

Write `AGENTS.md` and `README.md` for a reader who has not seen our conversations.
Start with the purpose of the project and the role of each repository or major
component. Explain names, acronyms, prerequisites and commands before relying on
them. Give enough context to understand why a technical rule exists; do not use
these files as shorthand notes about whatever implementation detail is currently
on your mind. Link to deeper documentation instead of making the introduction
incomprehensible or duplicating the entire design.

Each application has one main conversational agent and one durable conversation
history. Plugins may add tools or private specialist agents, but a specialist
must not change that main history directly. The server starts and monitors agent
processes, authenticates clients and exposes HTTP/WebSocket connections. The
browser runs media playback and device control on the user's machine. Keep
these responsibilities separate so a worker crash or client reconnect cannot
silently change the conversation or bypass device safety.

Use `modict`, the shared typed-mapping library, for structured data; inspect its
API before adding another model or conversion layer. The local checkouts at
`/home/baptiste/dev/modict` and `/home/baptiste/dev/codex-backend-sdk` (the
bridge to the AI backend) are writable shared dependencies. Changes there must
remain useful to other projects and backward-compatible. Do not introduce
The Harness-specific behavior into them.

The starter must remain an editable application that depends only on this
framework, not on The Harness. Its bootstrap must work after installing a built
Python wheel outside this checkout. Keep the public framework API small; remove
obsolete internal imports instead of adding compatibility shims. Verify changes
with focused tests, the full framework suite, a test of the built wheel outside
the source tree and, for UI changes, the starter's npm tests/build.

Create coherent local commits for verified changes. Do not publish or push
without an explicit request.
