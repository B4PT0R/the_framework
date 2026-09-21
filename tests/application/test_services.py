import asyncio

import pytest

from the_framework.server.composition.services import ServiceGraph, ServiceSpec


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
            ServiceSpec(name="api", service=Service("api", events), depends_on=("worker",)),
            ServiceSpec(name="worker", service=Service("worker", events)),
            ServiceSpec(name="media", service=Service("media", events), depends_on=("api",)),
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
            ServiceSpec(name="worker", service=Service("worker", events)),
            ServiceSpec(
                name="remote",
                service=Service("remote", events, fail_start=True),
                depends_on=("worker",),
            ),
        ])
        with pytest.raises(RuntimeError, match="cannot start remote"):
            await graph.start()
        assert events == ["start:worker", "start:remote", "stop:worker"]
        assert not graph.running

    asyncio.run(scenario())


def test_service_graph_rejects_missing_and_cyclic_dependencies():
    missing = ServiceGraph([
        ServiceSpec(name="api", service=object(), depends_on=("worker",)),
    ])
    with pytest.raises(ValueError, match="unknown.*worker"):
        missing.resolve_order()

    cyclic = ServiceGraph([
        ServiceSpec(name="one", service=object(), depends_on=("two",)),
        ServiceSpec(name="two", service=object(), depends_on=("one",)),
    ])
    with pytest.raises(ValueError, match="cyclic"):
        cyclic.resolve_order()


def test_noncritical_service_failure_is_reported_without_aborting_graph():
    async def scenario():
        events = []
        graph = ServiceGraph([
            ServiceSpec(
                name="optional",
                service=Service("optional", events, fail_start=True),
                critical=False,
            ),
            ServiceSpec(name="worker", service=Service("worker", events)),
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
            ServiceSpec(name="optional", service=optional, critical=False),
            ServiceSpec(
                name="downstream",
                service=downstream,
                depends_on=("optional",),
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
