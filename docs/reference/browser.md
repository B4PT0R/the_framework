# Browser observation and execution

`CHROMIUM` from `the_framework.plugins.browser` contributes tools and a provider.
The worker extension controls an application-root browser RPC host; it does not
own a browser per conversation. Browser windows/profiles and agent observation
have separate lifetimes. Closing observation must not close the user's tabs.

## Assemble and configure

Include `CHROMIUM` in `Application.plugins`. The plugin alone does not install a
browser, start an RPC daemon or authorize access to another desktop. The local
host/launcher must provide the browser service at `agent.application_root`.
An independent `agent.session_root` does not relocate that shared RPC root.
See [hosting](hosting.md) and the [first-run guide](../guides/first-application.md)
for launcher and browser preparation.

`BrowserConfig` declares `web_headless=False`, `ui_headless=False`,
`ui_app_mode=True`, viewport 1365×900 and `max_snapshot_elements=120`. These are
extension configuration, not evidence that an existing daemon adopted a change.
In the default RPC path, process startup controls the running service; source or
config edits do not reload that daemon. The explicitly injected service-factory
path constructs a controller owned by the extension and closes it on shutdown.

Two isolated persistent profiles/targets exist:

- `web`: arbitrary web browsing and user-driven sign-in;
- `harness_ui`: the application interface, retaining its protocol identifier even
  in applications with another name. It does not allow arbitrary `goto`.

The web profile is shared between headless/visible modes and launches with
`--disable-blink-features=AutomationControlled`. That flag suppresses Blink's
indicator, not Playwright control, and is not an identity-provider acceptance or
security guarantee. The UI profile does not use it. Treat browser content as
untrusted data and act only within user-authorized account/site scope.

## Observation contract without launching Chromium

This example injects a fake controller into the real extension. It verifies
observation and provider lifetime, not DOM extraction or real browser behavior:

```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework.agent import Agent
from the_framework.plugins.browser import ChromiumExtension
from the_framework.server.clients.browser import BrowserError

class EmptyBrowser:
    def __init__(self):
        self.calls = []

    def session_call(self, target, operation, *args, **kwargs):
        self.calls.append((target, operation))
        assert operation == "status"
        return {"open": False, "tabs": []}

with TemporaryDirectory() as directory:
    extension = ChromiumExtension(Agent(client=object()), runtime_root=Path(directory))
    controller = EmptyBrowser()
    extension.controller = controller
    assert asyncio.run(extension.chromium_state()) is None
    try:
        extension.status()
    except BrowserError:
        pass
    else:
        raise AssertionError("Actions must require observation")
    extension.open_session("web")
    observation = asyncio.run(extension.chromium_state())
    assert observation["observation_open"] and observation["tabs"] == []
    assert controller.calls == [("web", "status"), ("web", "status")]
    extension.close_session()  # no RPC and no tab closure
    assert asyncio.run(extension.chromium_state()) is None
    assert len(controller.calls) == 2
```

`open_session(target)` checks current status and activates observation without
launching Chromium or creating tabs. Empty state is valid. All action tools
require an explicit observation session. External UI/web-open events may navigate
the browser but never enable observation implicitly. Deactivation/shutdown clears
observation; reopening reads the tabs that exist then.

On each model step, async `chromium_state` samples current status in a thread. A
nonempty target yields formatted DOM plus an `Image` screenshot; empty targets
yield status only. Errors become an unavailable-state description, not invented
DOM. This is [ephemeral provider context](providers.md), not history or a chat
image artifact. If observation closes during sampling, its result is discarded.
Preserve needed conclusions before closing or navigating away: a later provider
view can replace the only current DOM/screenshot without undoing earlier work.

## Tool workflow

| Operation | Contract |
| --- | --- |
| `open_session`, `close_session` | Enable/disable observation, independent of windows |
| `status`, `select_tab(index)` | Current target state; indices start at zero |
| `open_web(url=None, new_tab=False)` | Web only; may launch/create/navigate |
| `goto(url)` | Web only; navigate active tab |
| `back()` | Navigate the observed target backward |
| `click`, `fill`, `select`, `press` | Act on fresh DOM handles; `press` can be global |
| `read_element(id, field, offset, limit)` | Read current `value`, `text`, `href`, `placeholder` in pages |
| `close_tab(index=None)` | Web only; selected or active tab |
| `close_web()` | Web only; all web windows/tabs, never the UI target |

An action result/status is not proof of business success. Check the next
observation or relevant application state. DOM IDs are opaque snapshot handles:
a fresh snapshot invalidates older IDs even when the page looks unchanged. Do
not guess hidden IDs or reuse handles after navigation/other view changes.

Long fields are shortened only when the daemon advertises full-field reading.
Use `read_element` with a fresh ID and follow `next_offset`; reading does not
refresh the DOM. When an older daemon lacks that capability, the provider says so
and keeps field values intact. Do not assume the reader exists merely because the
worker has a newer tool schema, or reload a running daemon just to clear that
warning. Snapshot element-count truncation is separate from long-field pagination.

Finish work requiring the current view before closing observation. Then use
`close_session` when moving on or awaiting the user. Close only disposable
agent-opened tabs; preserve user-owned/useful/uncertain tabs. `close_web` is not
observation cleanup: it destroys every web tab. `close_tab`/`close_web` do not
implicitly clear observation.

## Application UI, RPC and lifecycle limits

`BrowserService.open_application_ui(url)` opens the chosen application route.
Loopback and explicitly trusted HTTPS `application_ui_origins` are accepted.
Query/fragment credentials are kept out of Chromium launch argv and passed through
navigation. Both Unix and host transports use `open_application_ui`; source edits
do not update an already-running host. Do not copy bootstrap URLs into history.

Browser RPC cancels pending daemon operations when callers disconnect or time
out. Cancellation cannot undo a click, navigation, submission or file operation
already performed. Reobserve before retrying a side effect.

Opening the first web tab reuses Chromium's bootstrap page, including with
`new_tab=True`; later explicit new tabs preserve existing ones. Closing the last
web tab/window also closes that browser context, not the independent application
UI. Reopening uses the persistent web profile.

Closure first follows normal Playwright tab close. If the tab remains during a
reload, the service retries with `run_before_unload=True` and waits for the close
event, with five-second attempts. The explicit retry accepts before-unload through
Playwright's default dialog behavior. A stalled retry raises `BrowserError`
instead of destroying unrelated tabs/context. Recovery warnings omit page URLs
and contents. When native window closure disconnects the context before Chromium
exits, cleanup waits for the recorded browser process (30-second deadline), then
stops the driver. Caller cancellation does not cancel that profile cleanup.
This reduces reopen races; it is not a guarantee against crashes or forced kills,
and does not rewrite profile preferences to hide crash warnings.

## Implementation and checks

- Agent tools/provider/config: `the_framework/plugins/browser/agent.py`.
- Browser targets, snapshots, handles and lifecycle: `the_framework/server/clients/browser.py`.
- RPC transport/host: `the_framework/server/clients/browser_rpc.py`.
- Tests: `tests/general/test_browser_{observation,operations,read_rpc,rpc_lifecycle,session_lifecycle,close_recovery}.py`.
- Real browser checks use the repository's explicit browser setup; unit examples
  above require no Chromium process or external account.

[Reference index](README.md) · [Artifacts and vision](artifacts.md)
