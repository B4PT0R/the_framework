# Framework API simplification

## Decisions and evidence

| Objective | Result | Regression coverage |
| --- | --- | --- |
| One agent declaration | `AgentSpec` owns configuration, instructions, plugins, session/queue policy and completion settings. `agent_trigger(spec, ...)` owns selection and result handling only. | `test_agent_spec.py`, `test_framework.py`, worker/fleet tests |
| Internal wire representation | `WorkerProfile` remains a JSON execution payload, not a public facade export or a second source retained in AgentSpec. Older profile configuration fields are read at the worker boundary. | profile round trips, native coercion, real worker process tests |
| Explicit construction | `AgentResources` replaces implicit option dictionaries and parallel build keywords. Explicit resources bypass the factory completely. | factory identity, defaults, invalid contracts, configuration persistence tests |
| Concise plugin installation | `AgentPlugin` classes with an explicit name normalize to `Plugin`; full declarations retain policies, dependencies and services. | shorthand/full-form discovery, duplicate identities, no construction during compilation |
| Lifecycle simplicity | Existing Extension start/stop adapters suffice. Permanent restart callback composition happens at construction; absent restart remains absent. No new manager or lifecycle abstraction. | `test_runtime_composition.py`, service rollback and runtime shutdown tests |
| Model endpoint contracts | Request/response modict classes work alongside explicit JSON Schema. Native validators/defaults are applied at the existing HTTP boundary; field metadata supplies OpenAPI. | `test_endpoint_models.py`, endpoint registry, minimal application tests |

The actual product migrated its primary construction, simple plugin declarations,
memory curator and visual specialists. The minimal standalone application uses
model-based endpoint contracts. No application-specific policy was added to the
core, and no Pydantic DTO or alternate dispatch path was introduced.

## Deliberate limits

Final verification: `pytest -q --tb=short tests/application tests/agentic` reports
**900 passed**. This includes the isolated wheel and real worker subprocess
tests. The product compiles 14 plugins and 10 agents. Ruff's F401/F811/F821/F822/
F823/E9 checks and `git diff --check` pass for the changed core and endpoint tests.

- OpenAPI projection describes JSON field types, required fields and extra-field
  policy. It does not reverse-engineer Python validators or execute factories.
  Unsupported/recursive hints require an explicit JSON Schema. Runtime model
  validation remains authoritative for custom constraints.
- A wire profile cannot serialize Python closures or application services.
  Application-backed workers continue resolving the original namespaced spec.
- This work verifies source, transports and packaging with automated tests.
  It does not claim a production restart, real inference, voice or device run.
- No server restart, publish or push was performed for this goal.

The follow-ups already recorded in TODO.md remain separate from this API work:
YAML declaration errors, grouping usage records, and older architecture wording.
