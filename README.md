# The Framework

The Framework is a **framework for assembling full-stack agentic applications**,
designed to let the hosted agent modify its own application reliably without
interrupting its conversation with the user.

It is a Python library of reusable building blocks for the agent, its tools,
the application backend and its user interface. You assemble these into an
application with its own behavior, permissions and design. The Framework supplies
the shared machinery for keeping the conversation, running the agent and
replacing parts of the application as it evolves.

## What does that look like?

The included starter is a local desktop application: a Python backend and a
React interface displayed in Chromium. You talk to an agent, give it work and
receive text, files or other results in the same conversation.

That work can include changing the application itself. For example, you might
ask the agent to add a tool or improve how results appear in the interface.
With access to the source and the appropriate tools, it can edit the code,
run checks and prepare the change. Managed UI releases provide a preview before
publication and a rollback path; supervised backend replacement lets the
application resume from its saved conversation.

The Framework is built around this development loop: **ask, change, verify,
activate, continue**. Your application chooses which actions its agent may take
and how changes are approved and checked.

## How do you build with it?

Start from the editable starter, then change the agent’s instructions, add
features and adapt the interface. A feature can begin as one Python method
exposed as a tool and grow to include backend services, routes or another agent.
The same composition system connects these pieces and manages their lifetimes.

The [project explanation](docs/explanation/intention.md) develops this idea;
[Architecture](docs/explanation/architecture.md) introduces the building blocks
through the jobs they do.

## Try the editable starter

Requires **Python 3.12+**, **uv**, **Node.js/npm**, and the system libraries needed
by Playwright Chromium. In a Python environment of your choice:

```sh
python -m pip install b4pt0r-the-framework
the-framework bootstrap --code-dir /path/to/my-agent-code --data-dir /path/to/my-agent-data --run
```

Choose two empty or absent, non-overlapping directories: one for editable source,
one for private application data. Bootstrap prepares the project and opens its
window. Follow [Your first application](docs/guides/first-application.md) for
system prerequisites, model-account sign-in and your first change.

The distribution is `b4pt0r-the-framework`, the Python namespace is
`the_framework`, and the CLI is `the-framework`.

## Continue from your question

| You want to… | Read |
| --- | --- |
| Understand the purpose and the change workflow | [Building an application that can evolve](docs/explanation/intention.md) |
| See how the building blocks fit together | [Architecture](docs/explanation/architecture.md) |
| Understand how a conversation survives changes | [Continuity](docs/explanation/continuity.md) |
| Create and customize your application | [Your first application](docs/guides/first-application.md) |
| Give the agent a new tool | [Your first plugin](docs/guides/first-plugin.md) |
| Look up a precise API or runtime rule | [Technical reference](docs/reference/README.md) |

[All documentation →](docs/README.md)

## Project status

The Framework is an **alpha-stage** library; these docs follow the source in this
checkout. See the [changelog](CHANGELOG.md) for release differences. The starter
provides an editable local application, and the separate **The Harness** project
uses the library for a larger application.

Tools run with the permissions of their configured account. Keep the starter’s
server on loopback and its data private; the [security reference](docs/reference/security.md)
covers access policies for other hosting arrangements.

For source work, see [Development](docs/guides/development.md).
For opt-in immutable backends, see [Deployment and recovery](docs/deployment.md).
The project uses the [MIT license](LICENSE).
