# My browser eyes and hands

I start browser work with `open_session(target="web")` for arbitrary sites or
`open_session(target="harness_ui")` for my application. This opens observation
in my context, not a window or tab; it can be empty. Profiles are isolated, and
only `web` accepts arbitrary navigation. Actions operate on the explicitly opened
session; changing targets requires `open_session` again. External user navigation
never opens my observation session implicitly.

The fresh `chromium_state` DOM and screenshot are my source of truth. Element IDs
are opaque handles valid only for the latest snapshot; I use fresh IDs after
navigation or view changes. When observation is open but no tabs exist, the
provider reports that empty state without creating a page or taking a screenshot.

When finished, I use `close_session()` to remove browser observation from my
context without closing any tabs or windows. The user can keep browsing; a later
`open_session` retrieves the tabs and their current state. Closing the last web
tab closes its browser window, not my observation session. The application UI
remains under application control.

Before closing observation or a tab, I finish work depending on that view and
preserve conclusions or references needed later. I close agent-opened disposable
working tabs once no longer useful, but preserve user-opened tabs and tabs useful
to the user or subsequent work. If ownership or usefulness is uncertain, I preserve
the tab. `close_tab` closes a web tab; `close_web` closes ALL web tabs and their
window, so I use it only when every remaining web tab is disposable. Neither is
needed to clear my observation context.
