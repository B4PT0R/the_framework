# Documentation

The Framework lets an application keep a continuous agent conversation while
its execution, features and interfaces evolve. You do not need to learn every
runtime contract before trying it.

## 1. Understand the project

Read these in order for the short conceptual path. No setup is required.

1. [Intention and boundaries](explanation/intention.md) — the problem, goals,
   non-goals and the difference between ambition and delivered mechanisms.
2. [Architecture](explanation/architecture.md) — components, ownership and the
   reasons for the main boundaries, followed by one conversation’s journey.
3. [Continuity](explanation/continuity.md) — durable state, temporary context,
   restarts and what “persistent” does not promise.

## 2. Build something

1. [Your first application](guides/first-application.md) — prerequisites,
   bootstrap, first launch, first reply and the files to change.
2. [Your first plugin](guides/first-plugin.md) — add one small tool, understand
   installation versus agent binding, then grow only as needed.
3. [Development](guides/development.md) — work from source, verify changes and
   keep development, frontend publication and backend deployment distinct.

The [starter manual](../starter/README.md) covers everyday UI behavior, data,
voice, browser operation and advanced customization. It is a companion to the
first application guide, not prerequisite reading.

## 3. Look up a contract

Use the [technical reference](reference/README.md) by topic: application,
services, plugins, sessions, agents, events, security, context, artifacts,
client surfaces, realtime, conversation ingress and scheduling.
The [deployment guide](deployment.md) covers the separate, opt-in immutable
backend workflow. It is not needed to bootstrap a local application.

## Choose a shorter route

| You are… | Suggested route |
| --- | --- |
| Evaluating the idea | Intention → Architecture → scope/maturity in the root README |
| Trying a local agent | First application → starter manual as needed |
| Adding tools | First plugin → plugin/agent reference |
| Writing a client | Architecture → events → artifacts → ingress → security |
| Integrating backend services | Architecture → services → plugin composition |
| Investigating recovery | Continuity → application/sessions → deployment if used |
| Maintaining this repository | Development → the relevant reference → source/tests |

## Status and reading conventions

- **Explanation** gives reasons and a mental model, not exhaustive signatures.
- **Guides** give an ordered path and expected outcomes.
- **Reference** gives precise contracts; some examples are intentionally fragments.
- **Historical notes** record decisions and audits, not the current API contract.

The documentation targets the source in this checkout. The
[changelog](../CHANGELOG.md) helps reconcile release differences.
[Historical notes](history/README.md) are available for maintainers, outside the
learning path. The old [single-page guide](framework.md) remains a section-link
map so existing bookmarks still lead to their topics.
