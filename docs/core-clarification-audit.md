# Core clarification follow-up

## Changes and verification boundaries

- Usage payloads (`ResponseUsage`, `TokenDetails`) live in `agent/models/usage.py`.
  The session store imports that owner, not the context builder. Public agent
  exports remain; internal callers were migrated without compatibility aliases.
  Recovery, coercion, session and context tests cover the unchanged behavior.
- Invalid YAML raises a contextual `ValueError` naming the declaring callable
  and YAML location, with the parser exception chained. Invalid metadata shapes
  no longer disappear through falsey fallback. Plain descriptions and empty
  docstrings remain supported. Both tool and endpoint construction are tested.
- AGENTS.md now describes distributed capability ownership and authenticated
  remote clients, rather than a local-desktop-only runtime. Evidence:
  `server/clients/remote.py`, `the_harness/remote_composition.py`,
  `docs/remote-client-protocol.md`, and `test_client_remote.py` (exclusive leases,
  revocation, stale generations and remote ownership without local Chromium).
- Memory instructions now distinguish temporary working capacity from completion
  constraints, omit the obsolete entry-count cap and describe deferred embedding.
  Evidence: `plugins/memory/store.py::_validate_capacity`,
  `plugins/memory/curator.py::completion_constraint_error` and `complete_curation`,
  plus memory tests for soft capacity, reduction beyond hard capacity, deferred
  embeddings and completion repair. No runtime policy or personal data changed.

## Schema comparison: deliberate non-extraction

Tools and signature-derived endpoints already share `signature_parameters` and
`annotation_schema`. The separate HTTP model projection reads modict field
metadata, defaults and required/extra policy; it never executes default factories.
The modict README field and configuration contracts were checked locally.

The remaining visitors overlap on scalar/list/union syntax, but differ in real
contracts: callable annotation inference is best-effort and accepts tuple/set
shapes, whereas explicit HTTP model contracts reject unsupported hints, non-string
mapping keys and recursive models rather than silently understating validation.
Combining them now would add policy switches or callbacks to a small visitor.
No new generic abstraction is introduced solely to deduplicate those branches.

Comparison did uncover a nullable-enum defect in callable projection: adding null
to `type` while preserving a non-null `enum` still excludes null. Union projection
now uses `anyOf` for constrained branches. A JSON Schema behavior test verifies
null and declared enum members are accepted, but arbitrary values are rejected.

No SDK/modict patch, publication, server restart or deployment was needed.

## Final verification

`pytest -q --tb=short tests/application tests/agentic`: **914 passed**,
including isolated-wheel imports and worker-process tests. Focused Ruff checks
(F401/F811/F821/F822/F823/E9) and `git diff --check` pass. The nullable contracts
are tested by accepted/rejected values rather than a prescribed JSON layout.
