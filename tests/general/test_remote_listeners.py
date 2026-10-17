import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.clients.remote import ClientRemoteService


def test_remote_listeners_use_registration_order_and_snapshot_dispatch(tmp_path):
    service = ClientRemoteService(SimpleNamespace(), tmp_path / "clients.json")
    events = []
    connection = object()

    def first(current, payload):
        assert current is connection
        events.append(("first", payload))
        service.remove_application_event_listener(second)

    def second(current, payload):
        events.append(("second", payload))

    service.add_application_event_listener(first)
    service.add_application_event_listener(second)
    service.on_application_event(connection, {"name": "camera_frame"})
    assert [name for name, _ in events] == ["first", "second"]
    service.remove_application_event_listener(first)
    service.on_application_event(connection, {})
    assert len(events) == 2


def test_remote_authority_listeners_await_in_order_and_propagate_errors(tmp_path):
    async def run():
        service = ClientRemoteService(SimpleNamespace(), tmp_path / "clients.json")
        events = []

        async def release(connection):
            await asyncio.sleep(0)
            events.append("release")

        def stop_device(connection):
            events.append("stop")
            raise RuntimeError("device unavailable")

        service.add_capabilities_changed_listener(release)
        service.add_capabilities_changed_listener(stop_device)
        with pytest.raises(RuntimeError, match="device unavailable"):
            await service.on_capabilities_changed(object())
        assert events == ["release", "stop"]
        service.remove_capabilities_changed_listener(stop_device)
        await service.on_capabilities_changed(object())
        assert events == ["release", "stop", "release"]

    asyncio.run(run())
