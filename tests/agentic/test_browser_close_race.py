"""Empirical Chromium reload/close race (upstream Playwright #42068)."""
import asyncio
import os

import pytest

from the_framework.server.clients import browser

pytestmark = pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), reason='requires configured Chromium')


def test_closing_during_reload_keeps_other_web_tabs_and_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, '_PAGE_CLOSE_TIMEOUT', .2)

    async def run():
        service = browser.BrowserService(tmp_path, web_headless=True, ui_headless=True)
        try:
            ui = await service.session('harness_ui').new_page()
            await ui.set_content('<textarea>Retained draft</textarea>')
            other = await service.session('web').current_page()
            for _ in range(20):
                page = await service.open_web(new_tab=True)
                try:
                    await page.evaluate('() => { location.reload(); return new Promise(() => {}); }')
                except Exception as error:
                    assert 'navigation' in str(error).lower() or 'context was destroyed' in str(error).lower()
                assert await asyncio.wait_for(service.close_web_tab(-1), 3) == {'closed_index': 1, 'remaining_tabs': 1}
                assert page.is_closed() and not other.is_closed()
                assert await ui.locator('textarea').input_value() == 'Retained draft'
        finally:
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 30))
