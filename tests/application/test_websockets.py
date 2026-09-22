import asyncio

from fastapi.testclient import TestClient

from the_framework.agent import AgentSpec, SessionPolicy
from the_framework.server.composition.application import AgentApplication, Extension, build_application
from the_framework.server.api.endpoints import Principal
from the_framework.server.api.websockets import (
    serve_event_stream_until_disconnect,
    websocket,
)


class Security:
    async def authenticate(self, connection):
        if connection.query_params.get("token") == "secret":
            return Principal(id="socket")
        return None

    async def authorize(self, _principal, requirement, _connection):
        return requirement != "denied"


def application(name, **options):
    return AgentApplication(
        name=name, version="1", primary_agent=AgentSpec(
            name="primary", description="Primary test agent.", session=SessionPolicy.durable()
        ),
        **options,
    )


@websocket("/events")
async def events(socket):
    await socket.accept()
    await socket.send_json({"status": "ready"})
    await socket.close()


def test_declarative_websocket_uses_the_application_security_policy():
    app = build_application(application(
        "Sockets",
        security=Security(),
        extensions=(Extension(name="events", websockets=(events,)),),
    ))

    with TestClient(app) as client:
        with client.websocket_connect("/events?token=secret") as socket:
            assert socket.receive_json() == {"status": "ready"}


def test_declarative_websocket_rejects_missing_security_and_collisions():
    try:
        build_application(application(
            "Unsafe",
            extensions=(Extension(name="events", websockets=(events,)),),
        ))
    except ValueError as error:
        assert "requires a security policy" in str(error)
    else:
        raise AssertionError("missing WebSocket security was accepted")

    try:
        build_application(application(
            "Duplicate",
            security=Security(),
            extensions=(
                Extension(name="one", websockets=(events,)),
                Extension(name="two", websockets=(events,)),
            ),
        ))
    except ValueError as error:
        assert "duplicate WebSocket endpoint" in str(error)
    else:
        raise AssertionError("duplicate WebSocket endpoint was accepted")


def test_push_stream_stops_promptly_on_disconnect_without_another_event():
    async def scenario():
        incoming = asyncio.Queue()
        sender_cancelled = asyncio.Event()

        class Socket:
            async def receive(self):
                return await incoming.get()

        async def send_events():
            try:
                await asyncio.Future()
            finally:
                sender_cancelled.set()

        task = asyncio.create_task(
            serve_event_stream_until_disconnect(Socket(), send_events)
        )
        await asyncio.sleep(0)
        await incoming.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 1)
        assert sender_cancelled.is_set()

    asyncio.run(scenario())
