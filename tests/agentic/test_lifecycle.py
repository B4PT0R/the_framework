"""Shared invocation contract for static services and dynamic plugin runtimes."""

import asyncio

import pytest

from core.server.composition.application import Extension
from core.server.composition.services import ServiceContext, ServiceSpec, invoke_lifecycle


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
