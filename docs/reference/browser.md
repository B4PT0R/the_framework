# Browser execution and observation

[Documentation](../README.md) / [Reference](README.md)

Use this for browser RPC ownership, observation sessions, DOM handles and
window/tab cleanup. Browser observation is temporary model context, not chat history.

## Browser and application roots

The browser uses the process-local `agent.application_root`, where a browser RPC
host must be running. This application root stays shared when an agent uses a
separate session directory. `agent.session_root` locates per-session resources,
including those of in-memory agents.
`BrowserService.open_application_ui(url)` opens the application's chosen route;
`application_ui_origins` permits explicitly trusted HTTPS origins in addition to
loopback. Query and fragment values are excluded from Chromium launch arguments
and passed through navigation. The isolated UI target/profile `harness_ui` stays
unchanged for saved browser sessions. The wire operation is
`open_application_ui` on both Unix and host transports; the former product-specific
`open_harness_ui` operation is no longer accepted. A daemon predating the canonical
operation must be upgraded with its callers; source edits do not reload a running
daemon.

The `web` browser uses the same dedicated persistent profile and launches with
`--disable-blink-features=AutomationControlled` in both visible and headless modes,
so sessions established through user-driven sign-in can be reused in either mode.
This suppresses Blink's automation indicator; it does not remove Playwright control
or guarantee acceptance by an identity provider, including in headless mode.
The independent application UI does not use this flag.

The agent's observation session is separate from browser windows and tabs.
`chromium.open_session(target="web")` (or `target="harness_ui"`) enables ephemeral
DOM/screenshot context for that target without launching Chromium or creating a
tab. It can observe an empty browser; the provider then reports only its empty
status. All agent browser actions require an explicit observation session and
operate on its selected target; web-only actions reject a UI observation session.
`chromium.close_session()` disables the provider without contacting the browser,
leaving all windows and tabs available to the user. Reopening observation reads
whatever tabs now exist, including changes made while observation was closed.
External navigation does not implicitly enable agent observation. `close_tab`
and `close_web` remain separate, explicit web tab/window destruction operations;
they do not close the agent's observation session. Observation starts closed on
extension construction and is cleared on deactivation/shutdown.

Browser DOM handles are opaque and valid only for their latest snapshot; taking a
new snapshot invalidates all older handles, including hidden/truncated elements.
If an RPC caller disconnects or times out, the daemon cancels its pending operation
instead of leaving an orphan task. Cancellation cannot undo an action that Chromium
has already performed.
Opening the first web tab reuses Chromium's bootstrap page, even with
`new_tab=True`, so no spare `about:blank` tab remains. Subsequent new-tab requests
still create separate tabs; existing tabs (including deliberately blank ones) are
preserved. The web browser closes automatically when its last tab closes, whether
through an agent tool or a native/user action. Its next opening starts one fresh
tab using the same persistent profile. The independent application UI is unaffected.

Tab closure first uses the ordinary Playwright close path. If Chromium acknowledges
it without destroying the tab during a reload (Playwright issue #42068), the daemon
retries that tab through `close(run_before_unload=True)` and waits for its actual
close event. Each attempt has a five-second deadline. Playwright's default dialog
handling accepts the before-unload prompt during this explicit close. If recovery
also stalls, the caller receives `BrowserError`; the daemon never closes the whole
context or the independent application UI to recover a single tab. A warning marks
the native retry in the browser daemon log without recording page contents/URLs.

Closing the last browser window can disconnect its persistent context before the
Chromium process has finished writing the profile. The session records that
process through CDP `SystemInfo.getProcessInfo` and waits for its exit with
`psutil` before stopping Playwright or reopening the profile. Cancelling a waiting
RPC does not cancel this cleanup. A stuck exit has a 30-second deadline and logs
a warning before driver cleanup; ordinary native closure preserves the clean-exit
state without rewriting Chromium preferences or hiding its crash warning.

---

[Reference index](README.md) · [Documentation paths](../README.md)
