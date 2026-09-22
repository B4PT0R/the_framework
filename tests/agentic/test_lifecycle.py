"""Shared invocation contract for static services and dynamic plugin runtimes."""

import asyncio

import pytest

from the_framework import endpoint
from the_framework.server.composition.application import Extension
from the_framework.server.composition.services import ServiceContext, ServiceSpec, invoke_lifecycle


@pytest.mark.parametrize("declaration", [Extension, ServiceSpec])
@pytest.mark.parametrize("action", ["start", "stop", "health"])
@pytest.mark.parametrize("adapter", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_lifecycle_has_one_invocation_contract(declaration, action, adapter, asynchronous):
    async def scenario():
        context = ServiceContext()
        calls = []

        class Service:
            pass

        service = Service()

        def call(*args):
            calls.append(args)
            return "result"

        async def async_call(*args):
            return call(*args)

        callback = async_call if asynchronous else call
        setattr(service, action, callback)
        spec = declaration(name="example", service=service,
                           **({action: callback} if adapter else {}))
        assert await invoke_lifecycle(spec, action, context) == "result"
        assert calls == [(service, context) if adapter else ()]

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_lifecycle_propagates_failure_and_cancellation(error):
    async def scenario():
        async def fail(_service, _context):
            raise error()

        spec = ServiceSpec(name="example", service=object(), start=fail)
        with pytest.raises(error):
            await invoke_lifecycle(spec, "start", ServiceContext())
        assert await invoke_lifecycle(spec, "stop", ServiceContext()) is None

    asyncio.run(scenario())


def test_http_endpoint_named_start_or_stop_is_not_a_lifecycle_hook():
    calls = []

    class Service:
        @endpoint("post", "/feature/start")
        def start(self):
            calls.append("start")

        @endpoint("post", "/feature/stop")
        def stop(self):
            calls.append("stop")

    async def scenario():
        service = Service()
        spec = ServiceSpec(name="feature", service=service)
        assert await invoke_lifecycle(spec, "start", ServiceContext()) is None
        assert await invoke_lifecycle(spec, "stop", ServiceContext()) is None
        assert calls == []
        explicit = ServiceSpec(
            name="feature", service=service,
            start=lambda _service, _context: calls.append("lifecycle"),
        )
        await invoke_lifecycle(explicit, "start", ServiceContext())
        assert calls == ["lifecycle"]

    asyncio.run(scenario())
