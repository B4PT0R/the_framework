"""Feedback on the current internal record and browser handle representation."""

import asyncio
from datetime import datetime, timezone

from modict import modict
from the_framework.agent.runtime.agentic_loop import PendingSteering
from the_framework.server.clients.browser import BrowserSession
from the_framework.server.clients.remote import ClientApplicationConnection, PendingPairing


def test_transient_records_keep_runtime_objects():
    async def scenario():
        future = asyncio.get_running_loop().create_future()
        queue = asyncio.Queue()
        socket = object()
        steering = PendingSteering(prompt="hello", future=future)
        connection = ClientApplicationConnection(client_id="client", websocket=socket, outgoing=queue)
        assert steering.future is future
        assert connection.websocket is socket
        assert connection.outgoing is queue
        connection.revoked = True
        assert connection.revoked is True

    asyncio.run(scenario())
    now = datetime.now(timezone.utc)
    pairing = PendingPairing(id="pair", secret_hash="hash", endpoint="https://example.test",
                             created_at=now, expires_at=now)
    pairing.failed_attempts += 1
    assert pairing.failed_attempts == 1


def test_browser_handles_are_attributes_not_configuration(tmp_path):
    first = BrowserSession(profile_dir=tmp_path / "first")
    second = BrowserSession(profile_dir=tmp_path / "second")
    context = object()
    first._context = modict.attr(context)
    assert first.context is context
    assert first.started and not second.started
    assert "_context" not in first
    assert "_cleanup_tasks" not in first
    assert first._cleanup_tasks is not second._cleanup_tasks
    assert first.extra_args is not second.extra_args
    assert first.viewport is not second.viewport
