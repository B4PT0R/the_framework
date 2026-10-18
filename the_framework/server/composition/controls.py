"""Agent-facing controls for the application plugin and surface hosts."""

import asyncio

from ..clients.browser_rpc import BrowserRPCClient, browser_socket_path


def _bind_application_controls(runtime, app, context):
    """Expose generic plugin and surface hosts to the canonical agent."""
    plugin_host = app.state.application.plugin_host
    surface_service = app.state.application.surface_service
    async def handle_plugins(method, payload, *, call):
        if method == "list":
            return {"plugins": plugin_host.status()}
        name = str(payload.get("name") or "")
        if method == "set_binding":
            return await plugin_host.set_binding(name, bool(payload.get("enabled")), call=call)
        raise ValueError(f"unknown plugin operation: {method}")

    async def handle_surfaces(method, payload):
        if method == "list":
            return surface_service.status()
        name = str(payload.get("name") or "main")
        if method == "build":
            surface = surface_service.surfaces.get(name)
            release = await surface_service.build(name)
            return {
                **release,
                "preview": f"{surface.preview_route}/{release.release}/",
            }
        if method == "preview":
            origin = context.get('origin')
            if not origin:
                raise RuntimeError('opening a preview requires origin in BuildContext')
            release = surface_service.preview_path(name, payload.get('release')).name
            path = surface_service.previews.issue(name, release)
            browser = BrowserRPCClient(browser_socket_path(context.get('machine_root') or context.require('data_root')))
            await asyncio.to_thread(browser.call, 'open_web', origin.rstrip('/') + path, new_tab=True)
            return {'surface': name, 'release': release, 'target': 'web', 'role': 'preview'}
        if method == "publish":
            return await surface_service.publish(name, payload.get("release"))
        if method == "rollback":
            return await surface_service.rollback(name)
        raise ValueError(f"unknown surface operation: {method}")

    runtime.bridge.register("plugins", handle_plugins, contextual="call")
    if surface_service is not None:
        runtime.bridge.register("surfaces", handle_surfaces)
