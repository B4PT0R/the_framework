import asyncio

import pytest

from the_framework import ServerExtension
from the_framework.server.composition.services import ServiceGraph
from the_framework.server.api.health import ApplicationHealthApi
from types import SimpleNamespace


class Service:
    def __init__(self, name, events, *, fail_start=False, fail_stop=False):
        self.name = name
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    async def start(self):
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"cannot start {self.name}")

    async def stop(self):
        self.events.append(f"stop:{self.name}")
        if self.fail_stop:
            raise RuntimeError(f"cannot stop {self.name}")


def test_service_graph_orders_dependencies_and_reverses_shutdown():
    async def scenario():
        events = []
        graph = ServiceGraph([
            ServerExtension(name="api", service=Service("api", events), requires_services=("worker",)),
            ServerExtension(name="worker", service=Service("worker", events)),
            ServerExtension(name="media", service=Service("media", events), requires_services=("api",)),
        ])
        context = await graph.start()
        assert context["worker"].name == "worker"
        await graph.stop()
        assert events == [
            "start:worker", "start:api", "start:media",
            "stop:media", "stop:api", "stop:worker",
        ]

    asyncio.run(scenario())


def test_service_graph_rolls_back_a_partial_start():
    async def scenario():
        events = []
        graph = ServiceGraph([
            ServerExtension(name="worker", service=Service("worker", events)),
            ServerExtension(
                name="remote",
                service=Service("remote", events, fail_start=True),
                requires_services=("worker",),
            ),
        ])
        with pytest.raises(RuntimeError, match="cannot start remote"):
            await graph.start()
        assert events == ["start:worker", "start:remote", "stop:worker"]
        assert not graph.running

    asyncio.run(scenario())


def test_service_graph_rejects_missing_and_cyclic_dependencies():
    missing = ServiceGraph([
        ServerExtension(name="api", service=object(), requires_services=("worker",)),
    ])
    with pytest.raises(ValueError, match="unknown.*worker"):
        missing.resolve_order()

    cyclic = ServiceGraph([
        ServerExtension(name="one", service=object(), requires_services=("two",)),
        ServerExtension(name="two", service=object(), requires_services=("one",)),
    ])
    with pytest.raises(ValueError, match="cyclic"):
        cyclic.resolve_order()


def test_noncritical_service_failure_is_reported_without_aborting_graph():
    async def scenario():
        events = []
        graph = ServiceGraph([
            ServerExtension(
                name="optional",
                service=Service("optional", events, fail_start=True),
                critical=False,
            ),
            ServerExtension(name="worker", service=Service("worker", events)),
        ])
        await graph.start()
        health = await graph.health()
        assert health["optional"]["status"] == "error"
        assert health["worker"]["status"] == "running"
        await graph.stop()
        assert events == ["start:optional", "start:worker", "stop:worker"]

    asyncio.run(scenario())


def test_failed_optional_dependency_is_not_exposed_or_started_downstream():
    async def scenario():
        events = []
        optional = Service("optional", events, fail_start=True)
        downstream = Service("downstream", events)
        graph = ServiceGraph([
            ServerExtension(name="optional", service=optional, critical=False),
            ServerExtension(
                name="downstream",
                service=downstream,
                requires_services=("optional",),
                critical=False,
            ),
        ])

        context = await graph.start()
        assert "optional" not in context
        assert "downstream" not in context
        assert graph.errors["downstream"].startswith(
            "application service dependencies are unavailable"
        )
        await graph.stop()
        assert events == ["start:optional"]

    asyncio.run(scenario())


def test_cancelled_start_rolls_back_started_services():
    async def run():
        events = []
        entered = asyncio.Event()

        class WaitingService(Service):
            async def start(self):
                entered.set()
                await asyncio.Event().wait()

        graph = ServiceGraph([
            ServerExtension(name="worker", service=Service("worker", events)),
            ServerExtension(name="waiting", service=WaitingService("waiting", events), requires_services=("worker",)),
        ])
        start = asyncio.create_task(graph.start())
        await entered.wait()
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        assert events == ["start:worker", "stop:worker"]
        assert not graph.running
        assert not graph.context.snapshot()

    asyncio.run(run())


def test_start_and_rollback_errors_are_both_preserved():
    async def run():
        graph = ServiceGraph([
            ServerExtension(name="worker", service=Service("worker", [], fail_stop=True)),
            ServerExtension(name="remote", service=Service("remote", [], fail_start=True), requires_services=("worker",)),
        ])
        with pytest.raises(ExceptionGroup) as caught:
            await graph.start()
        assert str(caught.value.exceptions[0]) == "cannot start remote"
        assert str(caught.value.exceptions[1].exceptions[0]) == "cannot stop worker"
        assert not graph.running

    asyncio.run(run())


@pytest.mark.parametrize("probe", ["error", "degraded", "raises"])
def test_live_health_failure_degrades_critical_service_and_recovers(probe):
    async def run():
        unhealthy = True

        def health(_service, _context):
            if unhealthy and probe == "raises":
                raise RuntimeError("backend unavailable")
            return {"status": probe if unhealthy else "ok"}

        graph = ServiceGraph([ServerExtension(name="backend", service=object(), health=health)])
        await graph.start()
        assert (await graph.health())["backend"]["status"] == "degraded"
        reply = await ApplicationHealthApi().health(
            SimpleNamespace(application=SimpleNamespace(service_graph=graph)),
        )
        assert reply["status"] == "degraded"
        assert graph.started == ["backend"]
        unhealthy = False
        assert (await graph.health())["backend"]["status"] == "running"
        await graph.stop()

    asyncio.run(run())
