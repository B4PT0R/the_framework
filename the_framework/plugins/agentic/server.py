"""Server-owned persistence and continuation delivery for agentic work."""

from pathlib import Path

from .service import AgenticService


def build_service(context, runtime):
    path = context.plugin("agentic").get("state_path")
    if path is None:
        path = Path(context.require("session_path")).parent / "agentic.json"
    def enabled():
        host = getattr(runtime, "plugin_host", None)
        return bool(host is not None and host.bindings.get("agentic", False))

    service = AgenticService(path, runtime.submit, enabled=enabled)
    runtime.bridge.register("agentic", service.handle, contextual=True)
    runtime.register_observer(service.observe)
    runtime.register_reset_hooks(suspend=service.stop, resume=service.start)
    return service
