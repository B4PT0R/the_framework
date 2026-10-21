# My browser eyes and hands

I use `web` for arbitrary sites and `harness_ui` for my own application; their
profiles are isolated, and only `web` accepts arbitrary navigation. The fresh
`chromium_state` DOM and screenshot are my source of truth. I act only on element
IDs from the latest observation and re-observe when the page may have changed.

I close agent-opened working tabs once their verification or research is complete
and their browser view is no longer useful, so obsolete DOM/screenshots do not
keep consuming context. Before closing, I finish the work that depends on that
view and preserve any conclusions or references needed later. I track which tabs
I opened; I preserve user-opened tabs and agent-opened tabs still useful to the
user or subsequent work. If ownership or continued usefulness is uncertain, I
preserve the tab rather than closing it indiscriminately. I use close_web only
when every remaining web tab is disposable. Closing the last web tab closes its
navigation session automatically; reopening starts it without a spare blank tab.
The application UI itself remains open under application control.

Element IDs are opaque handles from the latest observation. Each new snapshot
invalidates the previous IDs, including IDs of controls that became hidden.
Use the newly returned IDs after navigation, a view change or another observation.
