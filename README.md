# The Framework

Build an application around an agent whose conversation can outlive a browser
window, a worker process and changes to the application itself.

The Framework is a Python library for **persistent, extensible agent
applications**. It brings together a durable conversation, supervised execution,
modular features and authenticated clients. Your application supplies the agent’s
identity, instructions, access policy and user experience.

The intention is to make an agent application a coherent, evolvable system—not
a collection of scripts that loses its place whenever its interface changes.
The foundation should carry the continuity and lifecycle machinery, while the
application remains free to choose what its agent is and what it may do.

## Start at the level you need

| Your question | Start here |
| --- | --- |
| Why does this project exist? Is it for me? | [Intention and boundaries](docs/explanation/intention.md) |
| How do the parts fit, and why are they separate? | [Architecture](docs/explanation/architecture.md) |
| What survives a restart or context compaction? | [Continuity](docs/explanation/continuity.md) |
| Can I try it and make my own application? | [Your first application](docs/guides/first-application.md) |
| How do I add a feature? | [Your first plugin](docs/guides/first-plugin.md) |
| I need a precise API or runtime contract | [Technical reference](docs/reference/README.md) |

[All documentation and reading paths →](docs/README.md)

## The main pieces, in plain terms

- **Application** assembles the system and owns its server lifecycle.
- **AgentSpec** describes an agent; a **Session** remembers its conversation and settings.
- A supervised **worker** executes agent turns and writes that session.
- A **Plugin** owns a feature, from tools to server services when needed.
- A **client surface** presents the conversation; it is not the conversation’s owner.

A single application can host independent conversations. Private specialist
agents can help without becoming authors of the main conversation. Text chat
is assembled by the host; voice, memory, scheduling and browser control are
optional features rather than obligations for every application.

## Try the editable starter

Requires **Python 3.12+**, **uv**, **Node.js/npm**, and the system libraries needed
by Playwright Chromium. In a Python environment of your choice:

```sh
python -m pip install b4pt0r-the-framework
the-framework bootstrap --code-dir /path/to/my-agent-code --data-dir /path/to/my-agent-data --run
```

Choose two empty or absent, non-overlapping directories. Bootstrap copies
editable application source, prepares its environment and builds the UI.
It does not install OS packages or sign in to a model account. The window and
settings can start without model credentials; replies and optional hosted
features require a usable backend account. See the
[first-application guide](docs/guides/first-application.md) for prerequisites,
expected results, sign-in and recovery.

The distribution is `b4pt0r-the-framework`, the Python namespace is
`the_framework`, and the CLI is `the-framework`. The included
[starter](starter/README.md) is application source you can change, not a mandatory
frontend or a required agent personality.

## Scope and maturity

This is an **alpha-stage** library. These docs describe the checkout’s API;
published versions may differ. It is not a hosted service, a general sandbox,
or a promise of safe autonomous self-modification. Tools run with their
configured account’s permissions. The starter is local-only; do not expose its
port publicly as a substitute for a remote access policy.

The broader ambition is an application that can evolve through conversation
while preserving continuity. The library supplies reusable mechanisms toward
that goal, not a finished product policy or a guarantee that every change is
safe. The separate **The Harness** application is a larger consumer; the
Framework does not depend on it.

For source work, see [Development](docs/guides/development.md).
For opt-in immutable backends, see [Deployment and recovery](docs/deployment.md).
See also the [changelog](CHANGELOG.md) and [MIT license](LICENSE).
