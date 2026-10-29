# Develop the Framework without confusing source and runtime

Use this guide for library work. To customize a copied application instead,
start with [First application](first-application.md) and the
[Notebook tutorial](first-plugin.md). Repository rules live in
[AGENTS.md](../../AGENTS.md); the application/framework boundary is explained
in [Architecture](../explanation/architecture.md).

## Choose the correct environment

The package requires Python 3.12 or newer. Outside the shared workspace, install
this checkout into an explicitly chosen compatible environment:

```sh
python -m pip install -e ".[dev]"
```

In `the_harness_project/`, the parent owns one shared `.venv` and its `setup.sh`
installs all five editable members together. Do not create/synchronize a second
checkout-local environment or migrate the shared interpreter implicitly. From
this repository, use `../.venv/bin/python` or activate `../.venv`. Run tests from
**one repository at a time**, so import resolution and test modules stay local.
Workspace orchestration is not a dependency of the installed Framework wheel.

`pyproject.toml` owns package metadata, supported Python and runtime requirements.
The import is `the_framework`; the distribution is `b4pt0r-the-framework`.
Keep minimum compatibility requirements distinct from a particular release's
resolved dependency snapshot. Do not infer a fixed Playwright version from an
unversioned requirement: the installed driver requires its own matching browser.

## Understand the source boundaries

| Location | Responsibility |
| --- | --- |
| `the_framework/server/composition/` | Public declarations, application assembly and service graph |
| `the_framework/agent/` | Agent construction, registries, context, inference loop and worker protocol |
| `the_framework/agent/context/` | Canonical session, persistence, projections, model preparation and compaction |
| `the_framework/server/` | ASGI/security, runtime/services, clients, supervision and ingress |
| `the_framework/plugins/` | Complete reusable features, with worker bindings and optional server services |
| `the_framework/execution/`, `deployment/` | Explicit process targets and optional immutable backend hosting |
| `starter/` | Editable consuming application; not hidden Framework product policy |
| `examples/notebook/` | Complete documented feature spanning storage, HTTP and worker tools |
| `tests/general/`, `tests/agentic/`, `tests/fixtures/` | Durable contracts, evolving checks and test support |

Inspect declarations and **actual call sites**, not just names or old examples.
For a new feature, first decide whether it belongs to application policy, a plugin,
a server service, an agent extension, a client or a reusable Framework primitive.
Server services are not worker objects; private specialists do not share caller
history. A route staying installed when a binding is disabled is intentional.
See [Plugins](../reference/plugins.md), [Services](../reference/services.md) and
[Hosting](../reference/hosting.md) before adding lifecycle plumbing.

## Make and verify a coherent change

1. Read repository instructions and `git status --short`; preserve unrelated work.
2. Find the relevant implementation and consumers. State the intended observable
   guarantee and the process/storage owner before changing it.
3. Add/update focused tests. Prefer real public execution paths with deterministic
   doubles at external boundaries, not assertions about private implementation.
4. Run that batch, then the full Framework suite. If a shared public contract
   changes, verify its consuming applications separately.
5. Update the appropriate guide/reference and executable example. Remove obsolete
   names/paths instead of leaving contradictory parallel explanations.
6. Distinguish source checked, artifact built, candidate tested, release selected,
   published and running. Do not deploy or restart merely to finish a code edit.

The normal Python check from the repository root is:

```sh
python -m pytest -q tests/general tests/agentic
git diff --check
```

`tests/general` protects deliberately durable behavior, not a promise that young
APIs can never be redesigned. `tests/agentic` contains evolving empirical checks,
not a lower-quality dumping ground. Update superseded assumptions deliberately;
never drop a meaningful guarantee to hide a regression. See
[Tests](../../tests/README.md) for classification.

### Run documented behavior

```sh
python -m pytest -q tests/general/test_documented_agent_contracts.py \
  tests/general/test_documented_notebook.py tests/general/test_documentation_links.py
```

The first test executes complete Python-fence sequences from selected reference
pages, sharing definitions **within** each page and isolating pages from one
another. These are runnable examples, not fragments requiring undeclared names.
The Notebook tests also traverse real hosted workers and their bridge with a
scripted inference client. Neither requires a paid model call; neither proves
model quality, remote service availability or production browser permissions.
The [Notebook README](../../examples/notebook/README.md) explains the precise
levels exercised.

### Package independence

```sh
python -m pytest -q tests/general/test_framework_wheel.py \
  tests/general/test_framework_bootstrap.py
```

The wheel test uses `uv build --wheel`, extracts the package outside the checkout,
checks packaged resources and worker entrypoints, and bootstraps source without
preparing a live app. It rejects accidental product imports. It skips when `uv`
is absent; a skipped packaging check is not packaging evidence. Build dependencies
may require access to an installer cache/index; no model inference is involved.
The wheel includes the editable starter template but excludes its `dist`,
`node_modules` and Python caches. Do not make the starter depend on The Harness.

### Browser and frontend checks

For a UI change, run both JavaScript tests and a build:

```sh
npm --prefix starter/ui ci
npm --prefix starter/ui test
npm --prefix starter/ui run build
```

`ci` needs the lockfile's packages/cache and deliberately replaces `node_modules`;
run it only when preparing that dependency environment. Tests/build are separate
from publication. Browser tests never download browsers. Prepare the matching
runtime **explicitly** with the chosen environment, then select its cache:

```sh
PLAYWRIGHT_BROWSERS_PATH=/explicit/test-browser-cache python -m playwright install chromium
python -m pytest -q --browser-cache /explicit/test-browser-cache \
  tests/general/test_starter_browser_recovery.py
```

`tests/conftest.py` ignores inherited `PLAYWRIGHT_BROWSERS_PATH`; pass
`--browser-cache`, not just the environment variable, to pytest. With no explicit
cache, a missing default browser skips real browser checks. A missing explicitly
requested cache fails. Launch failures (including missing headless shell/OS
libraries) fail rather than silently skip. Browser tests can launch disposable
local servers/processes and write temporary data. They do not authorize interaction
with the user's open browser or microphone.

### Explicit live and Docker tests

These opt-ins are **not part of ordinary offline verification**:

- `STARTER_LIVE_TESTS=1`: starter text/tool/memory and voice tests can authenticate
  to an external backend and consume quota; voice also needs a built UI and
  Chromium's fake microphone. No physical microphone guarantee follows.
- `FRAMEWORK_DOCKER_TEST=1`: builds and runs disposable images against the real
  local engine. See [Deployment](../deployment.md#verification-and-portability).

Obtain appropriate authorization before enabling them. A deterministic fake
backend is not evidence that those tests pass, and a historical passing count is
not a substitute for checking the current checkout.

## Activating an application change

Changing editable library source does not hot-reload existing imported objects.
New workers/server processes must import the intended installed version. In an
immutable backend, editing `/workspaces` does not replace installed code: build,
validate, select and supervise a new image. For UI changes, build an isolated
candidate, inspect its authenticated read-only preview, then publish explicitly.
See [Surfaces](../reference/surfaces.md) and [Deployment](../deployment.md).

The supervisor and canonical session preserve continuity; a shell restart is not
a substitute for the application's durable turn-boundary restart mechanism.
Tests use disposable data. Never delete a user's session, journals, browser profile
or configuration to make a failing test/startup disappear. Record any unverified
activation or external dependency in the final report.

**If behavior differs from the guide:** use [Troubleshooting](troubleshooting.md)
to localize the boundary before changing code or credentials.
