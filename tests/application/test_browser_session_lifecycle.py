"""A persistent browser owns one driver, including failed and concurrent starts."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.clients.browser import BrowserSession


class Context:
    def __init__(self, failure=None):
        self.failure = failure
        self.closed = False
        self.listeners = {}

    def on(self, event, callback):
        self.listeners[event] = callback

    async def add_cookies(self, cookies):
        if self.failure == "cookies":
            raise RuntimeError("cookies failed")

    async def close(self):
        self.closed = True
        if callback := self.listeners.get("close"):
            callback()


class Driver:
    def __init__(self, failure=None, gate=None):
        self.failure = failure
        self.gate = gate
        self.context = Context(failure)
        self.entered = asyncio.Event()
        self.stopped = False
        self.chromium = SimpleNamespace(launch_persistent_context=self.launch)

    async def launch(self, **options):
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.failure == "launch":
            raise RuntimeError("launch failed")
        return self.context

    async def stop(self):
        self.stopped = True

    async def start(self):
        return self


@pytest.mark.parametrize("failure", ["launch", "cookies"])
def test_failed_start_releases_resources_and_can_retry(tmp_path, failure):
    async def run():
        drivers = []

        def factory():
            driver = Driver(failure if not drivers else None)
            drivers.append(driver)
            return driver

        session = BrowserSession(profile_dir=tmp_path, playwright_factory=factory)
        session.set_attr("initial_cookies", [{"name": "test"}])
        with pytest.raises(RuntimeError, match=failure):
            await session.start()
        assert drivers[0].stopped
        assert not session.started
        if failure == "cookies":
            assert drivers[0].context.closed
        await session.start()
        assert len(drivers) == 2
        await session.stop()
        assert all(driver.stopped for driver in drivers)

    asyncio.run(run())


def test_concurrent_starts_share_one_driver(tmp_path):
    async def run():
        gate = asyncio.Event()
        drivers = []

        def factory():
            driver = Driver(gate=gate)
            drivers.append(driver)
            return driver

        session = BrowserSession(profile_dir=tmp_path, playwright_factory=factory)
        first = asyncio.create_task(session.start())
        await asyncio.sleep(0)
        await drivers[0].entered.wait()
        second = asyncio.create_task(session.start())
        await asyncio.sleep(0)
        gate.set()
        try:
            await asyncio.gather(first, second)
            assert len(drivers) == 1
        finally:
            await session.stop()
        assert all(driver.stopped for driver in drivers)

    asyncio.run(run())


def test_cancelled_start_stops_driver(tmp_path):
    async def run():
        driver = Driver(gate=asyncio.Event())
        session = BrowserSession(profile_dir=tmp_path, playwright_factory=lambda: driver)
        pending = asyncio.create_task(session.start())
        await driver.entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert driver.stopped
        assert not session.started

    asyncio.run(run())


def test_delayed_close_of_old_context_keeps_new_session(tmp_path):
    async def run():
        drivers = []

        def factory():
            driver = Driver()
            drivers.append(driver)
            return driver

        session = BrowserSession(profile_dir=tmp_path, playwright_factory=factory)
        await session.start()
        old_close = drivers[0].context.listeners["close"]
        await session.stop()
        await session.start()
        try:
            old_close()
            await asyncio.sleep(0)
            assert session.started
            assert not drivers[1].stopped
        finally:
            await session.stop()

    asyncio.run(run())
