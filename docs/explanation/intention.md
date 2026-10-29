# Why a full-stack agent application?

A useful agent needs more than an inference loop: tools, persistent conversation,
application services, permissions and an interface through which people can act
on its results. The Framework supplies reusable contracts for those pieces.
The application chooses the agent's identity, product behavior and authority.

One intended use is an application that its hosted agent can help evolve while
continuing the conversation that motivated the change. That is an application
capability to compose and authorize—not a claim that every agent automatically
has source access or that self-written code is safe.

## A concrete development loop

Suppose a document assistant needs a side-by-side comparison view. The request
can lead to ordinary work in the existing application:

1. Inspect its editable interface and the server data contract.
2. Implement and test the comparison behavior.
3. Build an isolated interface candidate and inspect its read-only preview.
4. Publish the verified candidate when authorized, or discard it without changing
   the active release.
5. Continue discussing the documents in the same canonical conversation.

A later import feature might also need a backend service, worker tools and a
storage decision. Those have different owners and activation requirements.
Backend replacement uses the chosen supervisor; an immutable deployment tests
and selects an exact image before activation. UI publication is not backend
activation, and neither proves that a user saw the result.

This is what **full-stack** means here: agent behavior, interface, server and
integrations can participate in one development loop without being collapsed
into one process or one mutable global object. The editable starter is one
application; The Harness is another consumer, not a hidden Framework dependency.

## Continuity is a storage and lifecycle contract

Keeping a conversation through a change requires more than leaving a window
open. A canonical Session owns saved history/configuration/state; a worker owns
inference and tool execution; the server owns services and supervision; clients
own local rendering and media/device execution. Temporary observations and live
captions are not durable memory. Browser reconnection does not make arbitrary
external effects exactly-once.

[Architecture](architecture.md) explains these boundaries;
[Continuity](continuity.md) distinguishes reconnect, compaction, worker/server
replacement and recovery. Applications still need compatible storage changes,
backups, explicit permissions and verification of the active version. Code
execution capabilities act with their configured account's authority, not an
implicit safety sandbox.

## Start smaller than self-modification

First run a local application and change its instructions. Then add one useful
feature with tests. The [Notebook tutorial](../guides/first-plugin.md) follows a
feature across HTTP, persistent storage, worker tools and context. The
[development guide](../guides/development.md) describes verification and activation;
[Surfaces](../reference/surfaces.md) and [Deployment](../deployment.md) provide the
specific release contracts when those mechanisms are needed.
