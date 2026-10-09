"""Build-time contributions for optional producers of live context.

A context factory receives the controller. Contexts may implement synchronous
`start(projection)`, plus `ready()`, `flush()`, `handle_worker_output(output)` and
`close()` hooks. The latter hooks may return awaitables. Voice owns delivery
barriers; each feature owns its context, state and background-task cleanup.
"""
CONTEXT_FACTORY = "realtime.context_factory"
