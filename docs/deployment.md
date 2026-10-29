# Immutable backend deployment and recovery

Deploy an installed application as an exact Docker image, validate that image
without production data, then select it for the next supervised start. This is
an **optional host-owned workflow**, not what the local starter launcher does.
It neither installs Docker nor opens a public service. Start with
[First application](guides/first-application.md) for ordinary local use.

## Ownership and the release state machine

| Component / operation | Owns or changes | Does not do |
| --- | --- | --- |
| `BackendImage` / `build()` | Explicit trusted wheels/projects → full local `sha256:` image ID | Test, select, launch, push or prune |
| `Deployment.test(image, ...)` | Disposable container; durable proof for the exact image and normalized application options | Use production environment/workspaces/host services or send a model prompt |
| `Deployment.select(image)` | Record `selected`, requiring existing validation proof and a locally available image | Interrupt a turn, change `current`, or restart |
| `Deployment.serve(runtime)` | Foreground **host** supervisor; readiness, promotion and recovery | Run inside the backend, supervise indefinitely after unexpected failure, or roll back data |
| In-image runner | Factory, ASGI/worker lifecycle, authenticated probes; exit 75 for requested restart | Build images or access the engine |
| Application | Security policy, safe startup, storage compatibility, restart intent and device/remote effects | Delegate those policies to Docker or the validation flag |

`status()` reads the release record. `current` is the last committed healthy
image, `previous` its predecessor, `selected` the requested next image;
`validated` contains proofs, and `pending` journals an uncommitted transition.
A built or selected candidate is not necessarily running. A dead backend can
still be recorded as `current`: check runtime health, not just this ledger.
Backend activation and [UI publication](reference/surfaces.md) are independent.

## Build inputs, runtime inputs and secrets

Public imports are from `the_framework.deployment`: `BackendImage`,
`BackendRuntime`, `Deployment`, `DeploymentError`, `HostEndpoint`, `HostServices`.
Construction/imports do not contact Docker. The example below creates only a
recipe, using a temporary **placeholder** wheel; it does not build or install it.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework.deployment import BackendImage, BackendRuntime, Deployment

with TemporaryDirectory() as directory:
    root = Path(directory)
    wheel = root / "example_app-1.0-py3-none-any.whl"
    wheel.write_bytes(b"placeholder, not an installable wheel")
    recipe = BackendImage("example_app.server:create_app", wheels=(wheel,))
    runtime = BackendRuntime(
        token="test-http-only", browser_token="test-browser-only",
        port=8765, options={"feature_enabled": True},
    )
    releases = Deployment(root / "backend")
    assert releases.status()["current"] is None
    assert not releases.root.exists()  # Construction/status are inert.
    assert runtime.settings()["validation"] is False
```

In an application-owned deployment script, use **real packaged wheels** or
`projects=(Path("/explicit/app-project"), ...)`. Projects must contain
`pyproject.toml`; the host runs `uv build --wheel --out-dir ... PROJECT` for each.
Only the resulting wheels and generated metadata/Dockerfile enter the Docker
context—not the repository, home directory, dotenv or implicit credentials.
A wheel/build backend can contain secrets or execute code: audit packaging and
trust all supplied projects. Include an explicit Framework wheel/project when
using local changes not available from the index.

The default base is `python:3.12-slim`. `base` and OS `packages` are explicit
recipe inputs; no toolchain/browser/model runtime is provisioned implicitly.
Public Python dependencies resolve during the image build. This is **not a
reproducible dependency lock**; retain the exact tested image ID, not a tag.
The workflow performs no registry push or image pruning.

`BackendRuntime` supplies stable, distinct HTTP and browser credentials,
loopback `port`, optional exact HTTP(S) `origin`, numeric `user`, `environment`,
application `options`, optional host endpoint and explicit mounts. Secrets are
outside the image in a private settings file (0600 on POSIX); environment is
installed by the runner, not passed as Docker environment arguments. Docker and
host administrators remain trusted.

`options` is a string-keyed, JSON-compatible mapping (or `None` for empty); it
cannot override core factory inputs. Options are copied/normalized and recorded
in validation proofs: **do not put secrets there**. Port/UID reject booleans;
environment requires valid string keys/values. Mutable runtime mappings are
snapshotted/revalidated before serving. Host ownership/ACLs remain your job;
choose a private deployment root, separate from live development data.

## Application factory contract

The recipe names an importable `module:create_app` synchronous callable. The
runner calls it with keyword inputs:

| Input | Meaning |
| --- | --- |
| `data_root: Path` | `/data`, writable persistent runtime data or disposable validation tmpfs |
| `token`, `browser_token` | Separate provisioned credentials; application must actually enforce authentication |
| `origin` | Expected browser/HTTP origin; not an authorization policy by itself |
| `restart` | Async callback accepting a restart intent; requests graceful runner exit 75 |
| `validation: bool` | Disable application-owned external startup effects and pass through to `BuildContext` |
| `host` | `HostEndpoint`, passed only when explicitly configured; never in validation |
| Additional keyword inputs | Normalized application `options` |

Return the ASGI adapter from the declared `Application.build(...)`, providing
an explicit module reference and `BuildContext(data_root=..., origin=...,
restart=..., validation=...)`, plus the application's security and handshake.
Use the [security reference](reference/security.md) for the policy contract and
[hosting reference](reference/hosting.md) for lifecycle responsibilities.
The repository's [deployment fixture](../tests/fixtures/deployment_app/deployment_fixture/__init__.py)
is a complete headless factory with real Framework workers and test-only bearer
security; it is not a browser credential provisioning design.

Imports/factory construction must be free of external actions in **all** modes.
During validation, Framework blocks queued worker commands and skips directory
broadcasts. Your application must separately suppress host/device access,
remote registration and scheduled external effects. A factory ignoring this
contract is not made safe by setting `validation=True`.

## Offline example: validation is not activation

This sequence exercises real release-state persistence with a fake engine.
It checks selection, cleanup and failed-retest invalidation; the fake probe
**does not prove Docker, HTTP authentication or application readiness**.
No engine, listener, model account or existing data is used.

```python
from the_framework.deployment import DeploymentError

class ExampleEngine:
    def __init__(self):
        self.calls = []
        self.failed = False

    def run(self, *args, timeout=60):
        self.calls.append(args)
        return "test diagnostics" if args[0] == "logs" else ""

    def inspect(self, name, *, timeout=60):
        return {"State": {"Running": not self.failed, "ExitCode": 1}}

    def remove(self, name):
        self.calls.append(("remove", name))

image = "sha256:" + "a" * 64
with TemporaryDirectory() as directory:
    engine = ExampleEngine()
    releases = Deployment(Path(directory) / "release", docker=engine)
    try:
        releases.select(image)
        raise AssertionError("untested image selected")
    except DeploymentError:
        pass
    assert releases.test(image, options={"feature_enabled": True})["status"] == "ok"
    assert releases.select(image) == image
    state = releases.status()
    assert state["selected"] == image and state["current"] is None
    assert state["validated"][image]["options"] == {"feature_enabled": True}
    create = next(call for call in engine.calls if call[0] == "create")
    assert create[create.index("--network") + 1] == "none"
    assert "--publish" not in create
    assert engine.calls[-2][0] == "remove"  # Followed only by selection's image inspect.

    engine.failed = True
    try:
        releases.test(image)
        raise AssertionError("failed candidate received proof")
    except DeploymentError:
        pass
    state = releases.status()
    assert image not in state["validated"] and state["selected"] is None
    assert engine.calls[-1][0] == "remove"
```

A real `test(image, session=Path(...), options=..., timeout=90)` creates a
network-disabled, readonly-root container with dropped capabilities,
no-new-privileges, bounded memory/CPU/PIDs and tmpfs `/data`/`/tmp`. Its only
mount is a temporary settings file, with fresh credentials. Optional `session`
copies **only** the explicitly supplied canonical JSON file, never production
settings, task/goal/restart journals, workspaces or host/browser secrets.
The complete probe checks unauthenticated rejection, authenticated health,
status/config/session object responses and preservation of a copied session ID.
It never sends an inference prompt.

Cleanup is mandatory even after failure; cleanup failure cannot grant proof.
A retest invalidates prior proof/selection before probing; a failed candidate
stays unvalidated. Invalid inputs can fail before that invalidation. Concurrent
tests of the same image are refused. Proof binds **image ID and options**, not
production secrets, environment, mounts, external services or future schema
compatibility. Selection checks image proof; launch additionally requires the
runtime options to match. Proof changed during readiness prevents promotion.
This is a startup/auth/persistence probe, not a model/tool acceptance suite or a
hostile-code sandbox.

## Serving, restart and recovery

After explicit build → test → select, call `releases.serve(runtime)` from a
foreground **host process**, not a worker or the image. It acquires a single
supervisor lease for the root, publishes HTTP only on `127.0.0.1:<port>`, and
runs the container with the configured numeric UID, readonly installed code,
writable `root/data` and `/tmp`, dropped capabilities and no-new-privileges.
Production networking is not validation's `--network none`. Remote clients
require an independently authorized proxy/private route; this module opens none.

The supervisor journals stopping/starting, drains an incumbent, starts the
target and commits `current`/`previous` only after authenticated readiness.
A failed drain refuses replacement rather than killing an active turn.
A failed candidate restores the last healthy image **and its options**, without
an infinite retry loop. Interrupted promotions restore the known-good image on
the next host launch; failed recovery retains its journal. First launch has no
fallback. Unexpected container exit stops the supervisor with an error; inspect
and relaunch explicitly. Keep prior images available locally.

**Image rollback never rolls back data.** A candidate can write production data
before readiness fails. Backups, migrations and schema compatibility remain
application responsibilities.

The application's durable turn-boundary restart action calls the supplied
`restart` callback after finishing the turn. Graceful ASGI shutdown exits 75;
the host then activates the selected image, or restarts current. Selection alone
does nothing to the running turn. Normal requested supervisor shutdown drains
and removes its backend; crashes/engine failures need explicit recovery.

### Explicit source and data mounts

`workspaces={"app": Path("/explicit/checkout")}` exposes editable source at
`/workspaces/app`; editing it does not replace installed site-packages. Build a
new wheel/image to activate backend changes. For narrower application paths,
`mounts={"/models": {"source": Path("/explicit/cache"), "readonly": True}}`
uses readonly by default. Sources must exist; targets must be normalized,
non-overlapping absolute application paths outside reserved OS/data/workspace
roots. These are explicit host authority, not a tool sandbox. Validation never
imports these mounts; files are not implicitly shared across execution targets.

## Optional trusted host services

A backend has no host authority by default. Explicitly compose `HostServices`
with fixed zero-argument `build`, fixed validation session/options, optional
browser service and optional named execution recipes. Deployment RPC accepts
build/test/select/status—not arbitrary commands, paths, Docker flags or sockets.
Accepted operations finish after client disconnection; after timeout, inspect
status before retrying. Restart is not a host RPC operation.

Set `BackendRuntime.host=HostEndpoint(("container-reachable-private-host", port),
token=..., browser_token=...)`. Bind the host listener to an **explicit private
IP**; wildcard/public binds are rejected. This is authenticated private TCP,
**not TLS**. Control/browser credentials must differ from each other and from
backend HTTP/browser credentials. Never expose `0.0.0.0` or docker.sock to solve
routing. Container `127.0.0.1` is not the host; choose an authorized engine
interface deliberately. Docker Desktop routing is not configured automatically.

The optional browser stays host-owned across backend replacement. Its separate
credential and fixed method allowlist do not authorize execution. Observations
transfer frozen screenshot bytes; backend screenshot paths are not host write
capabilities. See [Browser](reference/browser.md).

For execution, compose `ProcessHost` with explicitly registered recipe callables
and pass `HostServices(execution=..., execution_token=...)`; configure the same,
separate `HostEndpoint.execution_token`. Nothing is enabled by default. A recipe
receives an inherited socket descriptor and returns worker argv. The backend
requests only its registered name. `launch_process(..., target="local"|"host")`
never falls back between targets; protocol traffic and bounded process control
use separate connections. Validation cannot acquire host execution.

A Bash/Python recipe intentionally grants arbitrary execution with that host
account's permissions: transport/recipe allowlists are **not per-command
permissions or an OS sandbox**. EOF/shutdown retires processes; workers own child
cleanup. The bundled Bash worker cleans tracked jobs cooperatively, but SIGKILL,
machine failure and detached descendants can escape. See
[Execution](reference/execution.md) for ownership, cancellation and limits.

## Application-owned CLI

An app can call `the_framework.deployment.cli.main("my_app.server:create_app",
projects=(...), browser_factory=..., execution_factory=...)`, or supply an
explicit `BackendImage`. The CLI does not install a service or select implicitly.
For a module exposing that call:

```sh
python -m my_app.deployment --root /private/backend --project /explicit/dependency build
python -m my_app.deployment --root /private/backend --runtime /private/runtime.json test sha256:FULL_64_HEX
python -m my_app.deployment --root /private/backend select sha256:FULL_64_HEX
python -m my_app.deployment --root /private/backend status
python -m my_app.deployment --root /private/backend --runtime /private/runtime.json serve
```

`--wheel`/`--project` repeat. Status/test/select need no source checkout.
For test, `--runtime` forwards only `options`, not production environment/secrets;
`--session` supplies an optional canonical copy. Serve requires private runtime
JSON containing `BackendRuntime` fields with stable distinct credentials.
Do not commit that file or put secrets on the command line. With `host`, supply
`--host-bind`; the port comes from `host.address`. Host-enabled serve also needs
the fixed build recipe's source/wheel inputs available for host build calls.

`execution_factory` is a zero-argument host-service factory, called only for
serve with an explicit execution credential. A credential without a factory is
rejected before allocating browser/backend resources; without the credential,
the factory is never called. CLI cleanup closes composed resources after startup
failure/cancellation/shutdown. `HostServices` owns its execution service once
composed, while the CLI owns the optional browser's final stop.

## Verification and portability

Offline coverage lives in `tests/general/test_deployment*.py`: image staging,
proof invalidation, runner probes, options/credential validation, CLI ownership,
host authorization and supervisor rollback/recovery with fake engines. The
Python sequences on this page are also executed by the documentary tests.

Only with explicit permission to build/run Docker images:

```sh
FRAMEWORK_DOCKER_TEST=1 python -m pytest -q tests/agentic/test_docker_deployment.py
```

That opt-in suite uses real Framework workers and disposable sessions, checks
readiness/replacement/failed-start rollback, and removes only images it created.
It does not prune existing images. Linux Docker is the tested engine. Use of
`pathlib`, filelock, TCP and subprocess argument arrays is **not** a Windows or
macOS compatibility guarantee. UID/bind mounts, Desktop routing and platform
plugins need their own tests; Unix execution uses socketpair/pass_fds/signals.
