"""A stalled tab close recovers locally or fails without closing other tabs."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from the_framework.server.clients import browser


@pytest.mark.parametrize('recovery', [True, False])
def test_stalled_close_waits_for_actual_closure_and_preserves_other_tabs(tmp_path, monkeypatch, recovery):
    monkeypatch.setattr(browser, '_PAGE_CLOSE_TIMEOUT', .1)

    async def run():
        closed = asyncio.Event()
        calls = []
        other = SimpleNamespace(bring_to_front=AsyncMock())
        pages = [other]

        class Page:
            async def close(self, *, run_before_unload=False):
                calls.append(run_before_unload)
                if not run_before_unload:
                    await asyncio.Event().wait()  # Acknowledged but never closed.
                elif recovery:
                    def finish():
                        pages.remove(self)
                        closed.set()
                    asyncio.get_running_loop().call_later(.005, finish)

            def is_closed(self):
                return closed.is_set()

            @asynccontextmanager
            async def expect_event(self, event, *, timeout):
                assert event == 'close'
                yield
                await closed.wait()

        page = Page()
        pages.append(page)
        session = browser.BrowserSession(profile_dir=tmp_path)
        session.set_attr('_context', SimpleNamespace(pages=pages))
        session.set_attr('_active_page', page)
        if recovery:
            assert await session.close_tab(1) == {'closed_index': 1, 'remaining_tabs': 1}
            assert closed.is_set()
            other.bring_to_front.assert_awaited_once()
        else:
            with pytest.raises(browser.BrowserError, match='did not close; other tabs were preserved'):
                await session.close_tab(1)
            assert pages == [other, page]
            other.bring_to_front.assert_not_awaited()
        assert calls == [False, True]
        assert session.context.pages[0] is other

    asyncio.run(run())


def test_cancelling_close_does_not_start_recovery_or_close_another_tab(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, '_PAGE_CLOSE_TIMEOUT', 10)

    async def run():
        entered = asyncio.Event()
        calls = []

        class Page:
            async def close(self, **kwargs):
                calls.append(kwargs)
                entered.set()
                await asyncio.Event().wait()

        page = Page()
        session = browser.BrowserSession(profile_dir=tmp_path)
        pages = [object(), page]
        session.set_attr('_context', SimpleNamespace(pages=pages))
        task = asyncio.create_task(session.close_tab(1))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == [{}]
        assert len(pages) == 2

    asyncio.run(run())
