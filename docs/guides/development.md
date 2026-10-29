# Development and verification

[Documentation](../README.md) / Build / Development

Work from a checkout when changing the library or its bundled starter. This
guide covers the development environment, checks and the path from edited source
to an active version. For your own generated application, begin with
[bootstrap](first-application.md).

Read the repository’s [contributor instructions](../../AGENTS.md) before editing.

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

Verify the candidate produced by the relevant path, activate it deliberately,
then inspect the running result. Keep private data and access policies intact
through the change.

## Keep documentation at the right level

Start with the question the reader brings to the page. Define the thing and
show what it helps them do before introducing the mechanics. Use a concrete
application or change to connect the two.

The README defines the project and gives a first route in. Explanations develop
the mental model, guides walk through an outcome, and reference pages give exact
contracts. Keep prerequisites, permissions and recovery advice next to the actions
they affect. Check that examples and links still work when the API changes.
Design audits belong in the [historical index](../history/README.md).
