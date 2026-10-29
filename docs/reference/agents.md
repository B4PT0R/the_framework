# Agent construction and specialists

[Documentation](../README.md) / [Reference](README.md)

Use this for direct agent construction, process-local resources and private specialists. Hosted applications normally delegate construction to Application.

## Agent construction

`AgentSpec` declares what an agent is; `AgentResources` supplies the process-local
inputs needed to construct it: an inference client and initial configuration/state
defaults. The session owns subsequent changes and overrides these defaults on resume. It is not another plugin or application
composition layer. Put static defaults directly in `AgentSpec.configuration`.
Use a `resources(session_root)` factory only for process-local inputs such as
loading environment settings or creating an inference client. The argument is the
workspace directory (or None), independent of whether session history is stored
on disk. A memory-backed session never supplies a fabricated session file path. The generic worker
owns construction. Use explicit resources when constructing an agent directly.

```python
from the_framework.agent import AgentSpec, Session

agent_spec = AgentSpec(
    name="companion",
    description="Owner of the canonical conversation.",
    extensions=(memory, images, tools),
    configuration={"model": "your-model"},
    instructions=(identity_instruction,),
    projections={"mobile": mobile_projection},
    command_middleware=(measure_turns,),
)

session = Session.open(session_path)
agent = agent_spec.build_agent(session)
worker = agent_spec.build_worker(agent, receive, send)
```

Session lifetime is independent of the recipe. `Session()` keeps history in memory;
`Session(retain_history=False)` clears context between worker tasks. `Session.open(path)`
loads or creates a disk-backed session. Its `path` is runtime metadata, while
`retain_history` is canonical data persisted with the conversation. Reopening a
session restores that choice; launch defaults never replace saved behavior.
`Session.open(path, retain_history=...)` explicitly changes the loaded policy;
save the session to commit that deliberate change. A disk-backed session can
preserve settings without retaining task context.
`build_agent()` accepts a Session explicitly, or creates an in-memory one when omitted.
The same recipe can therefore construct agents with different session lifetimes.
Hosted application sessions are durable and retain history by default.
`open_session(durable=False)` keeps a session in memory; `retain_history=False`
independently selects task-local context. On resume, omit retain_history to use the
saved value; an explicit conflicting value is rejected instead of silently changing it.

The same `AgentSpec` declares a private specialist. Its trigger chooses hosting
and initial session behavior: `@agent_trigger(spec, durable=False, retain_history=False)`
by default. Set `retain_history=True` for context across tasks, `durable=True` for
persistence across worker restarts, or combine disk storage with task-local context.
`QueuePolicy` controls bounded FIFO/latest delivery
and durable task journaling. A plugin owns its specialists and calls them
through `agents.submit(name, prompt)`, which returns a request ID without waiting.
The worker routes results to `AgentTriggers.dispatch()` and the declared handler;
there is no `call()` alias suggesting a synchronous result or a second `publish()`
adapter. Another plugin can reach their capability only through an explicit versioned public
contract. The generic worker entrypoint resolves a selected public agent or a
namespaced specialist from `module:application`.

`@agent_trigger(spec, observes=..., item_kinds=..., conversation_roles=...,
input_item_field=..., durable=False, retain_history=False)` binds an `AgentSpec` to a result handler. The decorator
selects what to observe and the lifetime of the session it launches. Model/plugin settings live in the spec's
`configuration`, alongside its instructions and plugins. The trigger owns `queue`,
`idle_timeout_seconds`, `completion_tool` and `completion_retry_limit`: these are
invocation and result-delivery policies, not agent construction settings.
`activity_visible=True` controls transient client activity per specialist; set it
to `False` to hide progress and completion notifications without suppressing task
execution, result delivery or diagnostics. The memory curator uses this quiet policy. A completion
tool can be required without retries; a zero retry limit means one attempt. The spec's name identifies the specialist; a handler docstring fills
its description when the spec uses the default description.

For a single role, `instructions="Describe the evidence."` normalizes to one
`Instruction`. Use an explicit sequence of `Instruction` objects when names or
scopes matter; both forms use the same construction path.

A plugin that configures its specialist at load time replaces `trigger.spec`
with a revised `AgentSpec`; it does not mutate the shared class declaration.
The emitted worker profile is compiled from that current spec and the trigger's
selection. Agent construction applies declared configuration, then process-local
resource defaults, then resolved launch-profile settings when the worker has one.
Saved Session configuration is restored last. The launch profile is consumed
without retaining a competing source in AgentSpec; executable factories and
initializers stay on the application declaration. Explicit worker launch flags override profile defaults for newly created sessions;
a resumed session retains its canonical retain_history value.

Application compilation validates private identities and retains their AgentSpec
declarations in its plan. AgentSpec has no compiled `specialist_profiles` field.
After extension loading, the worker forms one declaration catalogue: each loaded
trigger supplies its configured spec and selection, replacing the corresponding
static declaration; explicit specialists without a trigger remain available.
The catalogue includes inactive bindings so the fleet can suspend or re-enable
execution without losing the configured declaration. Only then does `AgentTrigger.profile()` produce WorkerProfile
payloads for WorkerReady. Private specialists without handlers may use AgentTrigger directly in
Plugin.specialists to choose invocation policies; a bare AgentSpec is the shorthand
for defaults. Explicit declarations add to discovered class triggers, so adding a
private helper does not require redeclaring every decorated specialist. Named task
requests always route to their target; observes filters ambient events only.
Compilation retains those invocations, including for direct private
worker launches. AgentSpec does not compile fleet profiles. Worker constructors no longer merge two sets
of already serialized profiles.

The serialized specialist profile carries `durable` and `retain_history` defaults
for creating a session. WorkerReady reports the session's effective retain_history
value, so fleet completion retries follow saved behavior even when trigger defaults
have changed. The trigger owns these defaults and its queue/completion/idle policies; AgentSpec
contains only the reusable agent recipe.
Reconstructing a recipe from a profile does not attach hosting or history policy to it.
Task journaling (`durable_tasks`) remains independent of conversation persistence.
Durable tasks survive temporary worker startup or transport failures without
publishing an intermediate result to the caller. An explicit worker command
failure, interruption, or exhausted completion retry budget is terminal: its
failure enters the durable result outbox and the task leaves the pending queue.
It is not resubmitted forever against the same terminal command receipt.

`AgentSpec` is the public in-process construction recipe; `AgentTrigger` binds a
specialist's task/result handling to its plugin. `WorkerProfile`, under
`agent.models.worker`, is internal serializable fleet metadata, not a second
public declaration API. A profile is not a serializer for Python factories,
initializers or projection callables: application-backed workers resolve the
original namespaced spec. The advanced `build_agent(..., profile=...)` argument is
used at this process boundary to apply resolved settings after resource defaults;
ordinary application declarations do not need it. A profile with another agent's
identity is rejected. Imported role text becomes an ordinary spec instruction;
exporting uses the current instructions rather than the old profile text.

Terminal private-agent results enter a durable outbox only after their terminal
state is persisted. Delivery uses a stable command identity: a completed receipt
suppresses duplicate handling, while an interrupted delivery can run again after
restart. Acknowledgement removes the result from its durable outbox only after
successful delivery. Result handlers must tolerate redelivery if a crash occurs
after their side effects but before the completion receipt is persisted; this is
at-least-once delivery, not an atomic transaction across arbitrary plugin effects.

Middleware has the small async-generator contract
`middleware(agent, command, call_next)`. It adds cross-cutting behavior such as
timing or tracing without subclassing the worker. `SessionProjections` supplies
three reserved standard views: `display` keeps user/assistant messages and
artifacts, `activity` additionally keeps function calls, and `full` keeps all items.
These preserve item order and leave vision-only Image items out of chat.
Applications add pure named projections through `AgentSpec.projections`; select
one with `Application.build(session_projection=...)` when a custom view is needed.
Pagination selects canonical archive turns before projecting their items.
Responses and Realtime model input use separate projections.

---

[Reference index](README.md) · [Documentation paths](../README.md)
