# Versioned client surfaces

[Documentation](../README.md) / [Reference](README.md)

A client surface connects editable interface source to the releases served by
the application. It supports a build → preview → publish → rollback workflow,
so a candidate interface can be inspected before clients switch to it.

The conversation stays in the application’s session storage as UI releases
change. This page covers source conventions, build configuration and release
controls.

## Versioned client surfaces

An agent application's `build(reference, context, ...)` uses a single desktop UI
by default. Keep editable sources in `ui/` beside the module named in the explicit
`module:application` reference. The standard surface is `main`, built with
`npm run build` into `dist`, served at `/ui`, with the Playwright shell. The
starter follows this convention and declares no surfaces.

Omit `surfaces` (or use None) for this convention; use `surfaces=()` to explicitly
build a conversation without an interface. A missing default `ui/` is an error,
not a silent fallback to headless mode. Imports and worker compilation stay inert:
the default source is resolved only during server build, independently of cwd or
the Python call stack. Applications without declared agents have no implicit UI.

For a different source with the same desktop defaults, use
`surfaces=(ClientSurface(source=frontend_dir),)`. Other build systems, layouts and
multiple device interfaces remain explicit. `ClientSurface` makes each mutable
web template an application capability rather than a special Vite side effect:

```python
from the_framework import ClientSurface

surface = ClientSurface(
    name="main",
    source=project_root,
    build=("npm", "run", "build:surface"),
    artifact="desktop/.surface-build",
    routes=("/ui", "/remote-ui", "/quest-ui"),
    server_routes=("/ui/bootstrap",),
    shell="playwright",
)
```

Declare one `ClientSurface` per client interface when devices need different
applications: desktop, mobile and Quest can have separate sources, commands,
artifacts and routes. Every release operation takes its surface name; the system
plugin defaults to `main` only for convenience. Updating or rolling back one
surface leaves the others unchanged. Device selection and client-specific
capabilities remain application policy; all surfaces may share the same server
and durable conversation.

The surface service copies sources into an isolated workspace, executes the
fixed argv without a shell, verifies the artifact, hashes it and creates an
immutable release. Candidates are served on authenticated preview URLs.
Publication atomically swaps the persisted active release and notifies clients;
rollback swaps back without rebuilding. Build success is evidence only: the
agent remains responsible for inspecting the preview before publication.

The initial startup path uses the same declaration: if no published release is
available, the server adopts `source / artifact` during its lifespan. An external
packaged main bundle may be supplied with `BuildContext(ui_root=...)`. This only
seeds an unpublished surface; it never replaces an existing selected release.
Missing initial bundles leave the surface at HTTP 503 while the server and
conversation remain available for repair. If a selected release has disappeared,
an intact previous release takes precedence over any source bundle.

Active, previous and candidate selections persist together. Restarting preserves
an unpublished preview and never promotes it. Failed builds preserve all three
selections. New release directories become visible only after a complete copy;
incomplete staging directories are neither previews nor published releases.

The base chat UI has no deployment controls. The system plugin exposes
`system.surfaces`, `system.build_surface`, `system.preview_surface`,
`system.publish_surface` and `system.rollback_surface`. The preview tool opens a
new browser web tab and returns only surface/release metadata, keeping temporary
credentials out of conversation history. It needs `BuildContext(origin=...)`
and the local browser RPC service. Without an explicit release, it opens the
candidate or the active release.
HTTP controls remain available to integrations. Publication and rollback notify
connected clients to refresh; conversation history comes from the durable worker
session. A backend restart alone does not navigate the existing browser page.
`server_routes` explicitly reserves routes beneath a surface mount for an
application or plugin endpoint, such as a login/bootstrap exchange. Other
endpoints and WebSockets under the mounted path are rejected at build time.

One principal desktop owns the application bridge and primary event connection.
A second primary event connection is rejected with code 1013. Authenticated
previews receive concurrent event streams without claiming desktop ownership or
suppressing desktop notifications. They cannot open the application bridge.
The chat shows a read-only badge: drafts and local navigation remain testable,
but prompts, voice, configuration writes and deployment actions are denied.
Previewing never publishes a release or navigates the principal page.

Integrations can request `POST /api/v1/surfaces/{name}/preview` with an optional
`release` to obtain a one-use link valid for 30 seconds. Opening that link on the
configured origin sets an HttpOnly, SameSite=Strict preview cookie valid for eight
hours, then redirects to the selected release. Its private signing key persists
across server replacement; unconsumed links do not. Preview credentials carry
`*:read` and `surfaces:preview`, and HTTP access is limited to GET/HEAD API and
preview routes. The initial link and preview HTML disable caching and referrers.

The starter and Harness integrate the shared `preview_access`, `is_preview` and
`authorize_preview` helpers into their own Host/Origin and authorization policies.
Custom applications must do the same when exposing previews: validate the preview
cookie before ordinary desktop credentials, enforce safe HTTP methods and read
scopes, and supply `client_role="preview"` on the event handshake. Device-specific
mobile/Quest authority remains application policy; a preview grants no such role.

Middleware and mounted ASGI applications belong on a `ServerExtension`, so they are
validated and installed with the rest of the graph, whether that extension is
declared directly by the application or owned by a plugin. Mounted ASGI apps
handle their own request authentication; the framework's `@endpoint` security
policy does not automatically wrap arbitrary mounts. Product code should not
add routes, middleware or mounts after `Application.build()`.

---

[Reference index](README.md) · [Documentation paths](../README.md)
