# Documentation

These pages document the current checkout. An installed release can differ; use
its accompanying docs and [changelog](../CHANGELOG.md). Historical audits are
[separate](history/README.md), not another API reference.

## Start with runnable behavior

| You want | Read / run |
| --- | --- |
| A local desktop application you can edit | [First application](guides/first-application.md) — install, login, reply, change instructions, resume |
| An agent without a server or account | [Agents](reference/agents.md#execute-a-complete-turn-without-a-model-account) — deterministic stream, real tool pipeline and history |
| A complete custom feature | [Notebook tutorial](guides/first-plugin.md) — SQLite, authenticated routes, worker bridge, tools, command, configuration/state, provider |
| An editable application already using the library | [Starter manual](../starter/README.md) — launcher, UI, settings, attachments, voice and private data |
| To extend the browser UI or implement a client | [Client integration](guides/client-integration.md) — authenticated feature requests, live display, reconciliation and activation |
| To diagnose a failing boundary | [Troubleshooting](guides/troubleshooting.md) — preparation, auth, workers, models, media and activation |
| To develop the library | [Development](guides/development.md) — environment, tests, wheel checks, activation boundaries |

The examples state what is actually exercised. A scripted backend verifies the
Framework's execution path; it does not prove account compatibility or the
quality of a model's decisions.

## Understand the runtime

- [Architecture](explanation/architecture.md): declarations, process ownership and
  the path from client input to committed history.
- [Continuity](explanation/continuity.md): what survives reconnect, compaction,
  worker replacement, server restart and loss of a device connection.
- [Application intent](explanation/intention.md): application-level choices and
  the optional self-modification workflow.

## Find the contract

| Area | Reference |
| --- | --- |
| Assemble and host | [Application](reference/application.md), [Plugins](reference/plugins.md), [Services](reference/services.md), [Hosting](reference/hosting.md) |
| Program an agent | [Agents / specialists](reference/agents.md), [Tools](reference/tools.md), [Extensions / state / commands / hooks](reference/extensions.md), [Providers](reference/providers.md) |
| Store and prepare a conversation | [Sessions](reference/sessions.md), [Context / compaction](reference/context.md) |
| Implement a client | [Conversation ingress](reference/conversation.md), [Events](reference/events.md), [Artifacts](reference/artifacts.md), [Security](reference/security.md) |
| Device and interface integration | [Surfaces](reference/surfaces.md), [Browser](reference/browser.md), [Realtime](reference/realtime.md) |
| Reuse bundled features | [Primitives](reference/primitives.md), [Execution / Bash](reference/execution.md), [Work / scheduling](reference/work-and-scheduling.md), [Composition examples](reference/examples.md) |
| Operate immutable backends | [Deployment and recovery](deployment.md) |

[Full reference and import map](reference/README.md). The former
[single-page guide](framework.md) keeps a short map for old links.
