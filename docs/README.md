# Documentation

Build an application whose agent can help change its tools, backend and interface
while keeping the conversation with its user. Start by seeing what that involves,
try the editable application, then look up the contracts needed for your changes.

## Understand what you are building

1. [Building an application that can evolve](explanation/intention.md) — a concrete
   example of changing the application through conversation, and the foundation
   that supports it.
2. [Architecture](explanation/architecture.md) — how to assemble an agent, its
   features and its interface, and why each has its own role.
3. [Continuity](explanation/continuity.md) — how the conversation is saved and
   resumed as interfaces, processes and model context change.

## Make it yours

1. [Your first application](guides/first-application.md) — create a local desktop
   app, get a reply, change its instructions and reopen the same conversation.
2. [Your first plugin](guides/first-plugin.md) — add a tool that reports the time,
   then see how a feature can grow into a backend service or integration.
3. [Development](guides/development.md) — work from source, check changes and
   choose how to activate them.

The [starter manual](../starter/README.md) explains everyday operation and
customization: settings, attachments, voice, browser access and private data.
Read the relevant section when you use that feature.

## Follow a specific task

| Your task | Reading path |
| --- | --- |
| Evaluate whether this fits your application | Project explanation → Architecture |
| Run a local agent | First application → starter manual as needed |
| Add a tool or integration | First plugin → plugin or services reference |
| Change the interface | First application → client surfaces reference |
| Build another client | Architecture → events → artifacts → conversation ingress → security |
| Investigate a restart or lost context | Continuity → sessions or context reference |
| Operate an immutable backend | Deployment and recovery |
| Maintain the library | Development → relevant contract → source and tests |

## Look up the details

The [technical reference](reference/README.md) groups exact declarations,
lifetimes and protocols by the task they support. Examples identify where they
belong in your application. [Deployment and recovery](deployment.md) covers the
optional immutable-backend workflow.

These docs follow the checkout. The [changelog](../CHANGELOG.md) records release
changes. Maintainers can consult [historical notes](history/README.md) for earlier
reasoning; the [old single-page guide](framework.md) redirects existing section
bookmarks to the current topics.
