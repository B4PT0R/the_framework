"""Install an assembled runtime's authenticated HTTP, socket and client surfaces."""

from ..api.endpoints import EndpointRegistry, endpoint_declarations
from ..api.websockets import WebSocketRegistry
from ..api.plugins import PluginHostApi
from ..api.surfaces import SurfaceApi
from ..clients.surfaces import SurfaceApplication, SurfaceService


def _paths_overlap(left, right):
    left = left.rstrip("/") or "/"
    right = right.rstrip("/") or "/"
    return (
        left == "/"
        or right == "/"
        or left == right
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
    )


def _path_within_mount(path, mount):
    mount = mount.rstrip("/") or "/"
    return mount == "/" or path == mount or path.startswith(f"{mount}/")


def _install_routes(app, runtime, all_extensions, *, application_only=False):
    application = app.state.application
    plan = runtime.plan
    build_context = runtime.context
    policy = application.access_policy
    is_root = runtime is application
    surfaces = plan.surfaces
    mounts = []
    middleware_entries = []
    for extension, _owner in all_extensions:
        middleware_entries.extend(
            (middleware, dict(options))
            for middleware, options in extension.middleware
        )
        for path, mounted_app, name in extension.mounts:
            if not path.startswith("/"):
                raise ValueError(
                    f"extension mount path must start with /: {extension.name}"
                )
            if any(_paths_overlap(path, existing) for existing, _, _ in mounts):
                raise ValueError(f"overlapping application mount: {path}")
            if any(
                _paths_overlap(path, route)
                for surface in surfaces
                for route in (*surface.routes, surface.preview_route)
            ):
                raise ValueError(
                    f"client surface route collides with application mount: {path}"
                )
            mounts.append((path, mounted_app, name))
    mount_prefixes = tuple(path for path, _, _ in mounts)
    reserved_surfaces = surfaces if is_root else application.plan.surfaces
    surface_prefixes = tuple(
        route
        for surface in reserved_surfaces
        for route in (*surface.routes, surface.preview_route)
    )
    allowed_surface_routes = {
        route
        for surface in reserved_surfaces
        for route in surface.server_routes
    }
    registry = EndpointRegistry(
        app,
        security=policy,
        validate_responses=application.validate_responses,
        application=application,
        reserved_prefixes=mount_prefixes,
    )
    socket_registry = WebSocketRegistry(app, security=policy)
    for extension, owner in all_extensions:
        for source in extension.endpoints:
            for declaration in endpoint_declarations(source):
                if declaration.path not in allowed_surface_routes and any(
                    _path_within_mount(declaration.path, prefix)
                    for prefix in surface_prefixes
                ):
                    raise ValueError(
                        f"application endpoint is shadowed by a mount: {declaration.path}"
                    )
                registry.add(declaration, owner=owner)
        for endpoint in extension.websockets:
            if any(_path_within_mount(endpoint.path, prefix) for prefix in mount_prefixes):
                raise ValueError(
                    f"application WebSocket is shadowed by a mount: {endpoint.path}"
                )
            if endpoint.path not in allowed_surface_routes and any(
                _path_within_mount(endpoint.path, prefix)
                for prefix in surface_prefixes
            ):
                raise ValueError(
                    f"application WebSocket is shadowed by a mount: {endpoint.path}"
                )
            socket_registry.add(endpoint, owner=owner)
    if plan.plugins and policy is not None and not application_only:
        for endpoint in endpoint_declarations(PluginHostApi(runtime)):
            registry.add(endpoint, owner="plugin_host")
    if surfaces:
        if policy is None:
            raise ValueError("client surfaces require a security policy")
        for endpoint in endpoint_declarations(SurfaceApi(runtime)):
            registry.add(endpoint, owner="surface_service")
    registry.install()
    socket_registry.install()
    for middleware, options in middleware_entries:
        app.add_middleware(middleware, **options)
    for path, mounted_app, name in mounts:
        app.mount(path, mounted_app, name=name)
    if surfaces:
        surface_root = build_context.get("surface_root")
        if surface_root is None:
            raise ValueError("client surfaces require surface_root in BuildContext")
        surface_service = SurfaceService(
            surfaces,
            surface_root,
            origin=build_context.get("origin"),
            notify=build_context.get("surface_notify", runtime.surface_notify),
            progress=build_context.get("surface_progress", runtime.surface_progress),
        )
        runtime.surface_service = surface_service
        for surface in surfaces:
            for route in surface.routes:
                app.mount(
                    route,
                    SurfaceApplication(surface_service, surface.name),
                    name=f"surface:{surface.name}:{route}",
                )
            app.mount(
                surface.preview_route,
                SurfaceApplication(
                    surface_service,
                    surface.name,
                    preview=True,
                    security=policy,
                ),
                name=f"surface-preview:{surface.name}",
            )
