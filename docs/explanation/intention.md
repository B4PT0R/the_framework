# Intention and boundaries

[Documentation](../README.md) / Understand / Intention

## The problem: an agent is more than one model request

A useful agent application has a life beyond its next reply. It needs to remember
a conversation, use tools, present results, reconnect clients, recover interrupted
work and change over time. Reimplementing that machinery for every application
makes it hard to tell who owns state and what a restart will destroy.

The Framework aims to provide that foundation without deciding the product.
Its central idea is **continuity with explicit ownership**: keep the conversation
durable, make execution replaceable, and let interfaces and features evolve
around it.

## What the project is trying to make possible

The broader ambition is a full-stack agent application that can evolve through
conversation. A user should be able to ask for new behavior or a better interface
without treating each change as a new, disconnected assistant.

That ambition implies more than adding tools:

- **Continuity:** distinguish the lasting conversation from temporary model
  context, client connections and processes.
- **Composable capabilities:** install features with clear ownership rather
  than stitching together separate registries, lifecycles and routes by hand.
- **Controlled evolution:** make interface candidates inspectable before
  publication and preserve the session through supervised server replacement.
- **Readable construction:** keep the ordinary path small and expose advanced
  mechanisms only when they solve a real need.
- **Application freedom:** keep identity, prompting, permissions, device behavior
  and visual design outside the reusable library.

These are design goals, not a claim that arbitrary self-modification is safe,
autonomous or fully implemented. An application must still establish permission,
verification and recovery policies.

## What exists in this checkout

The library provides declarations for applications, agents and plugins; durable
sessions; supervised workers; authenticated HTTP/WebSocket assembly; private
specialists; and versioned client surfaces. Bundled optional plugins cover such
features as memory, registry, scheduling, voice and browser control.

The editable starter puts these pieces together into a local desktop application.
It includes application instructions and personality choices; those are examples
to change, not policies imposed by the framework. The separate The Harness
application exercises a larger composition using the same public primitives.

## What it deliberately does not decide

The Framework is not a hosted model service, a universal agent personality or a
mandatory user interface. It does not infer authorization from a task, guarantee
the correctness of generated code, or turn a shell tool into a sandbox.

Persistence does not replace backups. A successful delivery is not evidence that
a person read it. A successful UI build is not proof that the UI is usable.
Remote hosting needs an explicit access policy; the local starter is not a
public deployment recipe. Cross-platform behavior and optional backend features
must be verified in the target environment, not inferred from the architecture.

## Is it the right starting point?

It is useful when you want a long-lived agent application with tools, private
state and clients that can reconnect or evolve independently of execution.
You can also use its service composition without declaring an agent.
For a single stateless model request, the whole application stack may be more
than you need.

**Next:** [Architecture](architecture.md) explains the few boundaries that make
this possible. Or go directly to [Your first application](../guides/first-application.md).
