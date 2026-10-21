"""A persistent browser owns one driver, including failed and concurrent starts."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.clients.browser import BrowserService, BrowserSession


@pytest.mark.parametrize("web_headless", [False, True])
def test_sign_in_flag_is_shared_by_both_web_modes(tmp_path, web_headless):
    service = BrowserService(tmp_path, web_headless=web_headless)
    assert service.session("web").extra_args == ["--disable-blink-features=AutomationControlled"]
    assert service.session("web").profile_dir == tmp_path / "browser" / "profiles" / "web"
    assert service.session("harness_ui").extra_args == []


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


@pytest.mark.parametrize('next_action', ['start', 'stop', 'cancel_start'])
def test_native_close_finishes_profile_exit_before_driver_release_or_reopen(tmp_path, next_action):
    import threading
    from modict import modict

    async def run():
        drivers = []
        loop = asyncio.get_running_loop()
        waiting = asyncio.Event()
        exited = threading.Event()
        class Process:
            def wait(self, *, timeout):
                loop.call_soon_threadsafe(waiting.set)
                assert exited.wait(timeout)
        def factory():
            driver = Driver()
            drivers.append(driver)
            return driver
        session = BrowserSession(profile_dir=tmp_path, playwright_factory=factory)
        await session.start()
        session._browser_process = modict.attr(Process())
        drivers[0].context.listeners['close']()
        await waiting.wait()
        action = asyncio.create_task(session.stop() if next_action == 'stop' else session.start())
        try:
            await asyncio.sleep(0)
            assert not drivers[0].stopped
            assert len(drivers) == 1
            if next_action == 'cancel_start':
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await action
                assert not drivers[0].stopped
            exited.set()
            if next_action == 'cancel_start':
                await session.start()
            else:
                await action
            assert drivers[0].stopped
            if next_action != 'stop':
                assert len(drivers) == 2
                assert session.started
        finally:
            exited.set()
            await session.stop()
    asyncio.run(asyncio.wait_for(run(), 5))


def test_observation_bounds_stalled_capture(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from the_framework.server.clients import browser

    async def run():
        cancelled = asyncio.Event()

        async def snapshot(**kwargs):
            return {"url": "about:blank"}

        async def screenshot(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        service = browser.BrowserService(tmp_path)
        session = SimpleNamespace(snapshot=snapshot, screenshot=screenshot)
        monkeypatch.setattr(service, "session", lambda target: session)
        timeout = asyncio.timeout
        budgets = []

        def short_timeout(seconds):
            budgets.append(seconds)
            return timeout(0.01)

        monkeypatch.setattr(browser.asyncio, "timeout", short_timeout)
        with pytest.raises(browser.BrowserError, match="observation timed out after 5 seconds"):
            await service.observe("web", tmp_path / "capture.png")
        assert budgets == [5]
        assert cancelled.is_set()

    asyncio.run(run())
