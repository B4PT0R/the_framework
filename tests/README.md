# The Framework tests

Classify tests by the guarantee they protect and its intended lifetime:

- `general/`: deliberately durable behavior and stable API/application contracts.
  Assert observable guarantees rather than incidental implementation details.
- `agentic/`: empirical feedback for ongoing agent-driven work, experiments and
  evolving implementation choices. These checks may be revised or removed.
- `fixtures/`: shared doubles, browser setup and subprocess helpers, without
  collected tests. Test modules must not import other test modules.

Session recovery, atomic persistence, plugin lifecycle, composition, authentication,
HTTP/WebSocket protocols and wheel/starter independence belong in `general`.
Current internal record layouts, dependency-helper traversal order, tokenizer
mappings, prompt wording and recipe snapshots belong in `agentic`.

A unit, integration, browser or live test can belong to either layer. Authorship
is irrelevant. Keeping a contract here does not forbid an intentional redesign:
update the relevant consumers and contract together when that design changes.

During each related batch, review agentic checks for obsolete assumptions,
redundancy and coupling to implementation details. Remove those that no longer
provide useful feedback. Promote a check to `general/` when its guarantee is
intentionally adopted; never delete a durable guarantee just to hide a regression.

Run the complete Python suite from the repository root:

```sh
uv run pytest -q
```

To select a layer, append `tests/general` or `tests/agentic`. Both are collected
by the default command; no special pytest marker is needed. Existing opt-in
live/browser requirements still apply.

JavaScript tests use the same split under `starter/ui/tests/`.
Run `npm test --prefix starter/ui` from the repository root.
Node discovers both layers, including newly added agentic tests.
