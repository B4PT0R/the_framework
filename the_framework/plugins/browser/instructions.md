# My browser eyes and hands

I use `web` for arbitrary sites and `harness_ui` for my own application; their
profiles are isolated, and only `web` accepts arbitrary navigation. The fresh
`chromium_state` DOM and screenshot are my source of truth. I act only on element
IDs from the latest observation and re-observe when the page may have changed.

I may close ordinary web tabs or the web session. The application UI itself remains
open under application control.

Element IDs are opaque handles from the latest observation. Each new snapshot
invalidates the previous IDs, including IDs of controls that became hidden.
Use the newly returned IDs after navigation, a view change or another observation.
