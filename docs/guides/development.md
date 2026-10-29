# Development and verification

[Documentation](../README.md) / Build / Development

Use this path to work on the Framework or its starter from a checkout.
For an independent application, start with [bootstrap](first-application.md).
Read this repository’s [contributor instructions](../../AGENTS.md) before changes.

## Choose the right environment

Standalone development can use a virtual environment of your choice with Python
3.12+. Install the checkout from its repository root:

```sh
python -m pip install -e ".[dev]"
```

In the shared development workspace, use its root `.venv` and workspace installer
instead of creating competing member environments or reinstalling one member
independently. Run commands from the repository that owns the change. Do not
assume a sibling checkout exists for standalone package users.

## Run the starter from source

Requires Node.js/npm and Chromium’s system libraries in addition to Python:

```sh
npm --prefix starter/ui ci
npm --prefix starter/ui run build
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" python -m starter
```

Run from the Framework repository root. Use a private `--data-dir` when you
want isolated application state. On Linux, Playwright’s `install --with-deps
chromium` can install OS libraries, but requires appropriate administrative
permission; that is a separate deliberate operation.

## Verify the boundary you changed

For Python behavior:

```sh
python -m pytest -q tests/general tests/agentic
```

For starter UI behavior:

```sh
npm --prefix starter/ui test
npm --prefix starter/ui run build
```

The [starter verification section](../../starter/README.md#verification-and-boundaries)
has targeted browser tests and explicitly opt-in model/voice checks. A passing
unit test is not evidence that a model account or physical microphone works.
Use isolated data, and do not run quota-consuming or audible tests implicitly.
Keep the explicit `PLAYWRIGHT_BROWSERS_PATH` consistent for browser tests;
reinstall that browser after updating Playwright.

For documentation changes, check local links and anchors, parse complete Python
examples and execute their non-network construction where possible. Distinguish
editing fragments from standalone examples. Review commands for their working
directory, prerequisites and side effects.

## Package without publishing

With the Python build frontend installed:

```sh
python -m build
```

This produces distributions in `dist/`; it does not publish them. The wheel
contains the typed library, runtime prompt resources and editable starter
source, not npm dependencies or a prebuilt browser UI. Wheel-isolation tests
verify packaged imports/resources separately from checkout behavior.

## Keep three change paths distinct

1. **Source editing and testing:** changes a working tree, not an active release.
2. **Frontend release:** build a candidate, inspect its authenticated read-only
   preview, then publish deliberately. Rollback restores the preceding surface.
3. **Backend replacement:** reconstruct server/worker resources through the
   appropriate supervised lifecycle. Immutable deployment is an optional separate
   workflow described in [Deployment and recovery](../deployment.md).

A frontend publication does not publish Python packages or select a backend.
A successful backend build is not a tested or selected image. None of these
operations authorizes public exposure or deletion of private data.

## Keep documentation at the right level

Explain reasons in `explanation/`, ordered tasks in `guides/` and exact contracts
in `reference/`. Update topic pages when the API changes. Keep the root README
an invitation and orientation, not a second reference manual. Design audits
belong in the [historical index](../history/README.md); their proposed APIs must
not be mistaken for shipped behavior.
