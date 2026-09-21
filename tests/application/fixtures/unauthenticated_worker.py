"""Simulate missing credentials without reading or changing the user's login."""

from codex_backend_sdk import OpenAI

from harness_core.agent.runtime.worker_process import main


def missing_credentials(self, *, interactive=True, force=False):
    assert not interactive, "workers must never launch an interactive OAuth flow"
    raise RuntimeError("No usable stored Codex credentials; interactive login required.")


OpenAI.authenticate = missing_credentials
main()
