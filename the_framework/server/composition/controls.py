"""Agent-facing controls for the application plugin and surface hosts."""

from pathlib import Path


def bind_application_controls(runtime, app, *, ui_root=None):
    """Expose generic plugin and surface hosts to the canonical agent."""
    plugin_host = app.state.application.plugin_host
    surface_service = app.state.application.surface_service
    if ui_root is not None and surface_service.active_path("main") is None:
        resolved_ui_root = Path(ui_root).expanduser().resolve()
        if resolved_ui_root.is_dir():
            surface_service.bootstrap("main", resolved_ui_root)

    async def handle_plugins(method, payload):
        if method == "list":
            return {"plugins": plugin_host.status()}
        name = str(payload.get("name") or "")
        if method == "set_binding":
            return await plugin_host.set_binding(name, bool(payload.get("enabled")))
        raise ValueError(f"unknown plugin operation: {method}")

    async def handle_surfaces(method, payload):
        name = str(payload.get("name") or "main")
        if method == "build":
            surface = surface_service.surfaces.get(name)
            release = await surface_service.build(name)
            return {
                **release,
                "preview": f"{surface.preview_route}/{release.release}/",
            }
        if method == "publish":
            return await surface_service.publish(name, payload.get("release"))
        if method == "rollback":
            return await surface_service.rollback(name)
        raise ValueError(f"unknown surface operation: {method}")

    runtime.application.register("plugins", handle_plugins)
    runtime.application.register("surfaces", handle_surfaces)
