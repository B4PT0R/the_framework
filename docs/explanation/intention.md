# Building an application that can evolve

[Documentation](../README.md) / Understand / Purpose

The Framework is a framework for assembling full-stack agentic applications,
designed so that the hosted agent can modify the application itself while
continuing its conversation with the user.

You build both sides of that experience: an agent that can do useful work, and
an application that gives it tools, displays its results and keeps the exchange
going. The reusable foundation handles conversation storage, execution and
component lifetimes. Your application defines the agent’s role, its permissions
and the interface people use.

## A request that changes the application

Suppose you use an agent to work on documents. You ask it for a better way to
compare the results side by side. That request may lead to a change in the
interface. Later, you ask for an import tool, which may require Python code and
a backend service.

With access to the relevant source and development tools, the hosted agent can
make those changes as part of the work. It can inspect the existing code, edit
it, run checks and prepare a version for use. You keep discussing the result in
the conversation where the need arose.

This is what **full-stack** means here: the application’s interface, backend,
agent behavior and integrations can all take part in that development loop.
The starter gives you a concrete beginning: a Python backend with a React
interface in a local Chromium window, editable as application source.

## Why build on a shared foundation?

Changing a running application raises practical questions. Where is the
conversation saved? Can the interface be replaced while keeping it? How do you
start a new service, inspect a candidate version or return to an earlier one?

The Framework supplies reusable, tested building blocks for those jobs:

- **Keep the exchange:** durable sessions store conversation and saved settings.
  A worker can resume execution around that record.
- **Add behavior:** plugins bring together a feature’s tools, backend resources
  and dependencies, with a common startup and shutdown lifecycle.
- **Try an interface change:** managed client surfaces separate building a
  candidate, previewing it, publishing it and rolling back.
- **Replace execution:** worker supervision and server-replacement mechanisms
  support resuming the application from durable state.

You can use the same foundation for applications with different purposes,
interfaces and agents. The editable starter shows one composition; The Harness
is a separate, larger application built on the library.

## How a change becomes part of the application

A useful change has several observable stages:

1. **Understand the request.** Decide whether it calls for an answer, use of an
   existing feature or a code change.
2. **Edit and check.** Work on the relevant source and test the behavior being
   changed.
3. **Prepare a candidate.** Build the interface or backend version that will
   actually run.
4. **Activate and inspect.** Publish the UI or replace the backend through its
   managed lifecycle, then check the result in the running application.
5. **Continue.** Keep the request, decisions and results in the same conversation.

The Framework provides mechanisms for these stages. The application connects
them to its agent’s tools and decides which actions need user approval. UI
releases use the [client-surface workflow](../reference/surfaces.md); backend
activation follows the chosen host, with an optional
[immutable deployment workflow](../deployment.md).

Verification and recovery make self-modification practical: test a candidate
before using it, retain a way back, and inspect interrupted work before retrying.
Tests and approvals should match the change’s effects. A tool that edits files
or runs commands acts with its configured account’s permissions.

## Start with one small change

Create the starter and change its instructions. Add a tool once you know what
you want the agent to do. Adapt the interface when an actual interaction calls
for it. The same application composition supports these increasingly substantial
changes, while the saved conversation provides their shared context.

**Next:** [Architecture](architecture.md) explains how to assemble these pieces.
Or follow [Your first application](../guides/first-application.md) to try them.
