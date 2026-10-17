# UI tests

`general/` protects durable user-facing behavior and frontend protocol contracts.
`agentic/` holds revisable implementation feedback, such as instrumentation and
performance experiments; create it when needed. Classify by the guarantee, not
by whether a test is a unit or integration test.

Run `npm test` from this UI package. Node discovers both layers. Review agentic
checks during related work, remove obsolete ones, and promote durable guarantees.
See the repository's `tests/README.md` for the shared discipline.
