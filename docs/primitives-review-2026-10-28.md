# Framework primitives: clarity and composition review

> Historical design note, not the current API contract. Start with the
> [documentation paths](README.md) or the [technical reference](reference/README.md).

Review date: 2026-10-28. Scope: public construction vocabulary, its main
implementation paths, and real uses in the Harness. This is not a line-by-line
audit of every bundled plugin or a claim of measured developer productivity.

## Reading map and conclusions

| Intent | Primitives and source | Assessment |
| --- | --- | --- |
| Declare an application | `Application` in `server/composition/application.py` | One resource owner and assembly operation; keep it distinct from session ownership. |
| Describe a feature | `Plugin`, `ServerExtension`, `BuildContext`, capabilities in `server/composition/declarations.py` | Agent binding, server lifetime and construction inputs are different responsibilities, not redundant wrappers. |
| Construct an agent | `AgentSpec`, `AgentResources` in `agent/spec.py` | Recipe versus process-local resources is useful; saved Session settings retain their explicit precedence. |
| Add behavior | `AgentExtension` and decorators in `agent/extensions/` | Small tools/providers/hooks are straightforward; their different output and lifetime contracts should not be hidden behind a generic contribution superclass. |
| Retain/project conversation | `Session` in `agent/context/session.py`, `Context` in `agent/context/builder.py` | Canonical atomic data and model projection/recovery remain separate. Do not shorten by conflating them. |
| Run a conversation | `SessionRuntime` in `server/runtime/session.py` | Server supervision/transport differs from worker-side `Agent`; no global current-session shortcut. |
| Invoke a specialist | `AgentTrigger`, `QueuePolicy` in `agent/extensions/specialists.py` | Advanced invocation policy should remain outside `AgentSpec`. No new convenience wrapper justified by this pass. |
| Expose HTTP/sockets | `endpoint`, `websocket` in `server/api/` | Declarative discovery and explicit authentication avoid hand-written routing in consumers. |
| Own an editable UI | `ClientSurface` in `server/clients/surfaces.py` | Defaults already cover the ordinary case; isolated build, preview and rollback are real guarantees. |

Consumer evidence: Harness `application.py`, `agent_definition.py`, and the
`documents`, `tts`, `client_remote` plugin declarations, plus TTS server wiring.
They cover agent-only features, lazy session services, named dependency injection,
versioned integrations, custom agent behavior and a custom client build.

The simple path is already reasonably short: declare an AgentSpec, choose bundled
Plugins, then build an Application with storage and security. Consumers need not
construct a SessionRuntime, service graph or worker payload themselves. More
advanced controls exist without being mandatory for that path.

## Change made

Separate the construction inputs and feature declarations from Application's
compilation/session/server assembly. The extracted definitions retain their
bodies unchanged. Bundled plugins, service lifecycle checks and the server facade
now import declarations from their owning module. ClientSurface comes directly
from its existing owner. The public `the_framework` / `the_framework.server`
construction API and Harness source remain unchanged; no manager, superclass,
forwarding service or compatibility layer was added.

This is progressive disclosure, **not a whole-codebase token reduction**:

| Source read in full | Before | After |
| --- | ---: | ---: |
| `application.py` | 1,294 lines / 11,557 tokens | 1,013 lines / 8,932 tokens |
| Feature declarations only | Mixed into the above file | 299 lines / 2,731 tokens |

Tokens use the local `o200k_base` encoding. Assembly-only full-file reading is
about 23% smaller; declarations can be inspected independently. Reading both new
files costs slightly more than reading the original (11,663 versus 11,557 tokens),
so these numbers are neither a universal context saving nor a performance gain.
Focused symbol reads remain preferable whenever possible.

## Deliberate non-changes

- No fusion of Plugin and AgentExtension: runtime installation and live binding
  have different lifetimes, including when tools are disabled.
- No generic registry/decorator hierarchy merely to remove a few similar lines:
  tool, provider, hook and specialist validation/execution semantics differ.
- No broad split of every long module: more navigation can outweigh smaller files.
  Application assembly remains substantial; further decomposition needs a concrete
  change scenario, not a file-length target.
- No removal of rarely used advanced options without consumer/contract evidence.
- No runtime publication, restart, remote push or changes to persisted schemas.

## Verification

- Framework: `../.venv/bin/python -m pytest -q tests/general tests/agentic`
  — **1,294 passed, 6 skipped** in 156.10 seconds.
- After adding seven public-facade identity cases:
  `../.venv/bin/python -m pytest -q tests/general/test_framework.py tests/general/test_runtime_models.py`
  — **49 passed** in 0.93 seconds, including all seven new cases.
- Harness consumer, tested separately from its own checkout:
  `../.venv/bin/python -m pytest -q` — **540 passed, 3 skipped** in 287.25 seconds;
  its working tree remains unchanged.
- AST comparison confirms the class/function definitions are unchanged across the
  extraction. Public facade tests confirm both import paths expose the same
  constructors. `git diff --check` passes.
- Pytest reports the existing unset `asyncio_default_fixture_loop_scope`
  deprecation warning; changing that policy is outside this review.

These are source/test checks, not validation of a newly deployed runtime. No
server restart, publication or remote push was performed.
