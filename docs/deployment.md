# Immutable Docker backends (opt-in)

This is a separate deployment path, not a replacement for the local development
launcher. Nothing installs a service, selects an image or switches a running
application on import. An existing Docker daemon running Linux containers and
`uv` are prerequisites; this module does not install them.

## Four operations, distinct responsibilities

```python
from pathlib import Path
from the_framework.deployment import BackendImage, BackendRuntime, Deployment

recipe = BackendImage(
    "my_app.server:create_app",
    projects=(Path("/explicit/app-project"),),
    # wheels=(Path("/explicit/local-dependency.whl"),),
    # packages=("ffmpeg",),  # only if required by this application
)
image = recipe.build()                  # full sha256 ID, not a mutable tag
releases = Deployment("/private/backend")
releases.test(image)                    # isolated startup, not selection
releases.select(image)                  # records next image, does not restart
releases.serve(BackendRuntime(          # foreground HOST process
    token="a-stable-private-token", browser_token="a-different-private-token",
    port=8765,
))
```

Wheels/projects are explicit trusted build inputs. Project wheels are built on
the host with `uv build --wheel`; only resulting wheels and generated metadata
enter the Docker context. No recursive repository copy, dotenv, home directory
or implicit credential forwarding. **A wheel can itself contain secrets or run
build code: audit its packaging.** Public Python dependencies resolve during the
image build; a custom `base`/explicit OS `packages` belong to the app recipe.
These inputs are not a reproducible dependency lock: the returned image ID is
the authoritative tested artifact. Include a modified local Framework wheel or
project explicitly when it is not yet available from the package index.

The installed application code and dependencies are immutable. An optional
`BackendRuntime(workspaces={"app": Path("/explicit/checkout")})` exposes editable
source at `/workspaces/app`, never over site-packages. Editing that checkout does
not change the running backend. The host recipe can build its next wheel from
it. General execution tools run inside the backend container unless the application
explicitly installs and authorizes the optional host execution service below.
This does not provision tool runtimes, compilers or package managers in images.

## Application factory

Provide a side-effect-free importable `module:create_app` callable. It receives
`data_root: Path`, `token`, `browser_token`, `origin`, `restart`, `validation`,
and optionally `host` when explicitly configured. It returns an ASGI application.
`BackendRuntime.options` adds JSON application-specific keyword inputs; core
inputs cannot be overridden. Keep options nonsecret: they are stored in release
validation records. Runtime secrets belong in the explicit token/environment
fields, outside images. Options must be a mapping with string keys, or `None`
for no options; false/zero/empty sequences are not mappings. Mapping values are
copied/revalidated at launch. Ports and numeric UIDs must be integers (not
booleans); environment keys/values must be valid strings. Invalid inputs fail
before any engine call.

Pass `validation` into the Framework `BuildContext`; disable application-owned
startup effects (host access, device actions, remote registration, scheduled
external work) in that mode. The runner starts a real worker/HTTP lifecycle but
Framework rejects queued worker commands and omits directory broadcasts in
validation. Factory construction/imports must not do external work regardless.
A factory that ignores this contract is not made safe by the validation flag.

## What a test proves — and does not

`test(image, session=Path(...), options={...}, timeout=90)`:

- uses the exact local image ID, a disposable readonly-root container, no network,
  dropped capabilities/no-new-privileges, bounded memory/CPU/PIDs and tmpfs data;
- mounts only a generated temporary settings file; uses fresh HTTP credentials;
- optionally copies **only** the explicit canonical session JSON, never task,
  goal or restart journals, production settings, workspaces, host/browser secrets;
- verifies unauthenticated rejection, authenticated health/status/config/session
  and the saved session identity; never sends a model prompt;
- removes the container even after failure; cleanup failure cannot grant proof.

The proof binds image ID and normalized application options. A failing retest
invalidates that image's old proof/selection. Tests for the same image cannot
race. Selection/launch fail closed without matching proof; a proof invalidated
during startup prevents committing promotion. State updates are atomic and
single-writer; a second supervisor for the same root is refused.

This is a startup/auth/persistence check, not a model/tool acceptance suite, a
host browser connectivity test, or a hostile-code security sandbox. It deliberately
excludes production environment, mounted workspaces, remote services and desktop.
Applications remain responsible for functional tests and explicit production
preflight checks. Image administrators and Docker administrators are trusted.

## Runtime and recovery

The host publishes HTTP **only on `127.0.0.1:<port>`**. External clients need an
independently authorized private proxy/route; this module exposes none. Production
settings (mode 0600 on POSIX) and persistent `root/data` remain outside the image.
The container runs as the configured numeric UID (host UID by default on Unix),
with readonly image files, dropped capabilities, writable data and `/tmp`.
Use a private root; host filesystem ACLs/ownership remain the operator's job.

`serve` journals stopping/starting before replacing an incumbent, drains it,
starts the target and records `current`/`previous` only after readiness. Failure
restores the last healthy image **and its application options**, without a retry
loop. Interrupted uncommitted promotions restore the known-good image on the next
host launch. Failed recovery retains its journal for another explicit attempt.
A first-launch failure has no fallback. A failed drain does not authorize killing
an active turn. Unexpected exits stop the supervisor with an error rather than
restarting indefinitely; investigate then relaunch it explicitly.

**Image rollback never rolls back data.** A candidate may write production data
before readiness fails. Schema compatibility, backups and migrations remain the
application's responsibility. Keep prior images available locally for recovery.
No registry push, garbage collection or automatic pruning is performed.

The application's existing durable restart action completes the current turn
and calls the supplied `restart` callback. The image runner exits 75 after graceful
ASGI shutdown; the external supervisor then activates the selected image (or
restarts current). Selecting alone does not interrupt the conversation. UI
candidate publication remains a separate operation; backend selection is not UI
publication. Stopping the foreground host supervisor drains/removes its backend.

## Optional trusted host services

`HostServices` exposes only host-registered `build()` (zero arguments), test,
select, status, the optional existing browser service and optional named process
recipes. Deployment/browser RPC never accepts arbitrary commands, paths, Docker
options or sockets. Validation session/options
are fixed by the host. Accepted deployment operations finish even if their client
disconnects; after a timeout inspect status before retrying.

Configure `BackendRuntime.host=HostEndpoint(("explicit-container-reachable-host", port),
token="control-secret", browser_token="browser-rpc-secret")`. These two tokens
must differ from each other and from backend HTTP/browser tokens. Bind the host
listener to an **explicit private IP**; wildcard/public binds are rejected. This
is private authenticated TCP, **not TLS**. It is unsuitable for untrusted networks.
No default listener or guessed route is opened. A container cannot reach a host
loopback listener through its own `127.0.0.1`; choose and authorize the appropriate
private engine interface deliberately. Docker Desktop routing differs; it is not
configured automatically. Never solve this by exposing `0.0.0.0` or docker.sock.

The browser stays host-owned across backend replacement. RPC reuses fixed browser
method allowlists and its separate credential. Observations transfer frozen image
bytes; backend screenshot paths never become host write paths. Workers discover
the explicit private endpoint through runner settings. The local Unix browser
path remains unchanged. Browser authorization does not grant execution authority.

### Explicit process execution

Applications may additionally compose `ProcessHost` from `the_framework.execution`
and pass it to `HostServices(execution=..., execution_token=...)`. Register a mapping
of trusted recipe names to local callables; each callable receives an inherited
socket descriptor and returns worker argv. Configure the same separate
`HostEndpoint.execution_token` in runtime settings. It must differ from control,
browser and backend credentials. No service is enabled by default. The generic
CLI accepts an application-owned `execution_factory`, but never supplies worker
recipes itself.

`launch_process(target, name, recipe)` requires `target="local"` (this backend's
environment) or `"host"` (the explicitly configured host). The host resolves only
its registered name, never a client-supplied command or module. A duplex channel
carries the worker's own protocol, including reverse RPC; process control uses a
separate bounded connection so busy user code can still be interrupted. Missing
host configuration, failed authentication or disconnection fails explicitly;
there is no local fallback. Isolated image validation cannot acquire this service.

A registered general-purpose Bash/Python worker deliberately **does** grant arbitrary
code execution with its host account's permissions. The transport and recipe list
are not a sandbox or per-command permission system. Protect the execution credential
accordingly; no root elevation or Docker socket is needed. Paths and interpreters
belong to the chosen target; file contents are not implicitly shared or transferred.

EOF or host-service shutdown retires a process; workers must clean up their own
children. The bundled Bash worker cleans tracked jobs on EOF/SIGTERM/SIGINT, and
its commands/jobs retain explicit target ownership. SIGKILL, machine failure and
children that deliberately detach can escape cooperative cleanup. This is not a
durable host job service. Unix socketpair/pass_fds/process-group and signal behavior
is tested on Linux only, not a Windows/macOS compatibility guarantee.

## CLI composition

Apps can call `the_framework.deployment.cli.main(factory, projects=(... ),
browser_factory=..., execution_factory=...)` or pass an explicit `BackendImage`
recipe. This composes
build/test/select/status/serve without application Docker boilerplate:

```sh
python -m my_app.deployment --root /private/backend --project /explicit/dependency build
python -m my_app.deployment --root /private/backend test sha256:FULL_64_HEX
python -m my_app.deployment --root /private/backend select sha256:FULL_64_HEX
python -m my_app.deployment --root /private/backend --runtime /private/runtime.json serve
```

`--wheel` and `--project` repeat; they add only explicit trusted inputs. Status,
test and select need no source checkout. For test, `--runtime` supplies only
`options`; production secrets/environment are not forwarded. `--session` is an
optional session copy for validation. For serve, the runtime JSON supplies the
`BackendRuntime` fields (keep it private, with stable distinct credentials). If
`host` is configured, `--host-bind` is required and the listener port is taken
from `host.address`. Runtime `workspaces` maps names to existing host directories.
Do not put secrets in a shell command or commit runtime JSON.

The optional `execution_factory` is a zero-argument callable returning a host
execution service (usually `ProcessHost` with explicit application recipes). It is
called only for `serve` with `host.execution_token`; a credential without a factory
is rejected before allocating browser/backend resources. Without the credential
the factory is never called. The CLI closes allocated resources on startup failure,
cancellation and shutdown; `HostServices` owns the execution service once composed.
This configures the host process, not an in-container fallback or an authorization
to run arbitrary workers.

## Verification and portability

Pure unit tests/imports do not need Docker. The explicit opt-in test is:

```sh
FRAMEWORK_DOCKER_TEST=1 uv run pytest -q tests/agentic/test_docker_deployment.py
```

It uses real Framework workers and disposable sessions, checks authenticated
readiness, image replacement and failed-start rollback, and removes only images
created by that test (never prunes existing images). Linux Docker is the verified
engine. New host deployment primitives use pathlib, filelock, TCP and subprocess
argument arrays rather than systemd/fcntl; this is **not evidence of Windows or
macOS compatibility**. UID/bind-mount behavior, Desktop routing and the rest of
an application's platform-specific plugins still need platform testing.

### Explicit application bind mounts

When named `/workspaces` are insufficient, `BackendRuntime.mounts` maps an
absolute container application path to `{"source": Path(...), "readonly": True}`.
Readonly is the default. Sources must exist; targets must be normalized,
non-overlapping application paths, not reserved OS/data/workspace roots.
Use narrow mounts for model caches, credentials or existing saved absolute paths;
this is explicit host authority, not a sandbox for untrusted tools. The installed
backend remains immutable. Isolated image validation never imports these mounts.
