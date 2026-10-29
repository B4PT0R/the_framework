# Versioned client surfaces

`ClientSurface` declares editable interface source, a fixed build command and
served routes. `SurfaceService` builds immutable candidates and separately selects
the release clients use. Neither a source edit nor a backend restart publishes a
candidate. Session data lives outside UI releases.

## Declare the interface

An application with agents and omitted `surfaces` (or `None`) uses `ui/` beside
the module in its explicit `module:application` build reference: surface `main`,
`npm run build`, artifact `dist`, route `/ui`, Playwright shell. The starter uses
this convention. Resolution happens during server build, not import or worker
compile; it does not infer a source from cwd or the call stack. A missing source
is an error. Use `surfaces=()` for deliberate headless operation. Applications
without agents have no implicit UI.

Declare explicit surfaces for another layout/build system or multiple interfaces.
Each surface has independent active/previous/candidate selections; device authority
and selection remain application policy, not consequences of a surface name.

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | `main` | Unique surface identifier |
| `source` | Required | Editable source directory, normalized absolute |
| `build` | `("npm", "run", "build")` | Nonempty argv, no shell interpolation |
| `artifact` | `dist` | Relative output directory; no `..` or absolute path |
| `routes` | `("/ui",)` | Application mounts |
| `server_routes` | `()` | Reserved application endpoints below those mounts |
| `preview_route` | `/__surface-preview/{name}` | Preview mount |
| `shell` | `playwright` | Optional client shell selection |
| `timeout_seconds` | `300` | Build subprocess deadline |
| `retain_releases` | `3` | Retention target, at least two; selected releases protected |

`server_routes` reserves paths such as `/ui/bootstrap` for a declared server
endpoint. Other HTTP/WebSocket declarations colliding beneath the surface mount
are rejected during assembly. Use `ServerExtension` for middleware and ASGI mounts;
an arbitrary mounted ASGI application still owns its authentication.

## Executable build, reopen and rollback

This self-contained example runs a tiny Python builder in temporary directories.
It exercises real subprocess/copy/hash/state behavior, but no web server, browser,
client refresh or installed application deployment. In an application, place the
`ClientSurface` declaration in `Application(surfaces=(surface,), ...)`; the
application owns the service lifecycle.

```python
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework import ClientSurface
from the_framework.server.clients.surfaces import SurfaceService

async def demonstrate_releases():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "ui"
        source.mkdir()
        (source / "build.py").write_text(
            "from pathlib import Path\n"
            "out = Path('dist'); out.mkdir(exist_ok=True)\n"
            "(out / 'index.html').write_text(Path('page.html').read_text())\n",
            encoding="utf-8",
        )
        (source / "page.html").write_text("<h1>Version one</h1>")
        surface = ClientSurface(
            source=source, build=(sys.executable, "build.py"), shell=None,
        )
        notifications = []
        service = SurfaceService((surface,), root / "releases-state",
                                 notify=notifications.append)
        first = await service.build("main")
        assert service.active_path("main") is None  # build is not publish
        assert service.preview_path("main").name == first.release
        await service.publish("main", first.release)
        (source / "page.html").write_text("<h1>Version two</h1>")
        second = await service.build("main")
        assert first.release != second.release
        assert service.active_path("main").name == first.release
        # In production, inspect the authenticated preview before this decision.
        reopened = SurfaceService((surface,), root / "releases-state")
        assert reopened.preview_path("main").name == second.release
        assert reopened.active_path("main").name == first.release
        await service.publish("main", second.release)
        await service.rollback("main")
        assert service.active_path("main").name == first.release
        assert notifications[-1]["type"] == "surface_rolled_back"
        assert not list(service.work_root.iterdir())

asyncio.run(demonstrate_releases())
```

Builds copy source to a temporary workspace, execute argv there and require an
`index.html` in the artifact. A literal `{artifact}` argv entry is replaced with
the isolated output path. Complete output trees are copied, hashed by names/bytes
and renamed into release storage before candidate state is saved. An unchanged
artifact reuses its hash. The workspace is removed after success or failure.

**This is not a security sandbox.** The builder runs with the server account's
permissions. Source symlinks remain symlinks; discovered `node_modules` directories
are linked back to existing dependencies rather than copied. Builds can therefore
mutate shared dependencies or reach external resources. Inspect commands and
source trust, and install dependencies separately; the service does not make
untrusted build scripts safe. The deadline covers subprocess execution, not an
end-to-end quota for source copy, hashing, disk or memory.

One build per surface can run at a time. Timeout/cancellation terminates the
subprocess group (TERM, then KILL after two seconds). Detached descendants are
outside that guarantee. A failed build preserves selected releases. The service
retains active, previous and candidate independently across restarts; publication
does not consume the candidate. Retention pruning protects selected releases and
is not a strict disk-count ceiling.

## Startup and repair

During lifespan, `initialize()` seeds an unpublished surface from
`source / artifact`; `BuildContext(ui_root=...)` may supply a packaged `main`
bundle. It never replaces an existing active selection. Missing initial compiled
files leave the surface unavailable (HTTP 503), not the conversation server down.
If an active release disappears, an intact previous release wins over a source
bundle. Release directories are not session storage; pruning a UI release must
not delete a conversation.

## Controls and preview security

The system plugin exposes `surfaces`, `build_surface`, `preview_surface`,
`publish_surface`, `rollback_surface`. Commands take a surface name (`main` by
default). The preview tool needs a configured origin and browser RPC; it opens a
new web tab and returns release metadata without putting its temporary credential
in conversation history. The base chat UI has no deployment buttons.

The HTTP equivalents are:

| Method and path | Required scope |
| --- | --- |
| `GET /api/v1/surfaces` | `surfaces:read` |
| `POST /api/v1/surfaces/{name}/build` | `surfaces:write` |
| `POST /api/v1/surfaces/{name}/cancel` | `surfaces:write` |
| `POST /api/v1/surfaces/{name}/preview` | `surfaces:preview` |
| `POST /api/v1/surfaces/{name}/publish` | `surfaces:write` |
| `POST /api/v1/surfaces/{name}/rollback` | `surfaces:write` |

Preview/publish accept optional `release`; omission selects candidate, then active.
Preview issuance returns a one-use link valid 30 seconds. Opening it on the trusted
origin exchanges it for an HttpOnly, SameSite=Strict cookie valid eight hours
(`Secure` on HTTPS). The signing key persists; outstanding one-use links do not.
Preview HTML and ticket exchange disable caching/referrers. Never log or present
these credential URLs as durable documentation links.

Custom applications must integrate `preview_access`, `is_preview` and
`authorize_preview` into their own host/origin/access policy, as the starter does.
Preview credentials use `*:read` and `surfaces:preview`; allowed API HTTP methods
are GET/HEAD, not writes. Supply `client_role="preview"` on event handshakes.
Preview sockets coexist with the primary desktop without claiming its bridge or
notification ownership. Draft editing and local navigation can be tested, but
prompts, voice starts, config writes and deployment actions are denied. A preview
is not a device pairing credential. See [security](security.md) and
[client transport](conversation.md).

Publication/rollback persist the selection then notify connected clients to
refresh. That notification is not proof of navigation or successful rendering.
An active spoken turn may defer refresh in a client. Backend restart alone neither
publishes a preview nor navigates an existing page. Inspect the candidate before
publication and check the running client afterward; rollback selects the previous
release without rebuilding and does not revert backend code or data.

## Implementation and checks

- Declaration/build/state/static serving: `the_framework/server/clients/surfaces.py`.
- Tickets/cookies/policy helpers: `the_framework/server/clients/previews.py`.
- HTTP controls: `the_framework/server/api/surfaces.py`.
- Application assembly: `the_framework/server/composition/`.
- Tests: `tests/general/test_surfaces.py`, `test_surface_previews.py`.

[Reference index](README.md) · [Hosting](hosting.md)
