"""Real Chromium control contracts; no user profile or inference."""
import asyncio
import os

import pytest

from the_framework.server.clients.browser import BrowserService, BROWSER_AGENT_ID_ATTR

pytestmark = pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), reason='requires configured Chromium')


def test_snapshot_replaces_hidden_and_truncated_handles_and_controls_remain_unambiguous(tmp_path):
    async def run():
        service = BrowserService(tmp_path, web_headless=True, ui_headless=True)
        try:
            page = await service.open_web(new_tab=True)
            await page.set_content('<button id="chat">Chat</button><button id="other">Other</button><section hidden id="settings"><button onclick="this.textContent=\'Done\'">Retour</button></section>')
            session = service.session('web')
            first = await session.snapshot()
            old_ids = [e['id'] for e in first['elements']]
            await page.evaluate("document.querySelector('#chat').hidden=true;document.querySelector('#settings').hidden=false")
            second = await session.snapshot(max_elements=1)
            assert not set(old_ids).intersection(e['id'] for e in second['elements'])
            assert await page.locator(f'[{BROWSER_AGENT_ID_ATTR}]').count() == 1
            final = await session.snapshot()
            back = next(e for e in final['elements'] if e['name']=='Retour')
            assert await (await session.locator(back['id'])).count() == 1
            await session.click(back['id'])
            assert await page.get_by_text('Done', exact=True).count() == 1
            assert await (await session.locator(old_ids[0])).count() == 0
        finally:
            await service.stop()
    asyncio.run(asyncio.wait_for(run(), 15))


def test_closing_web_tabs_preserves_other_pages_and_independent_ui(tmp_path):
    async def run():
        service = BrowserService(tmp_path, web_headless=True, ui_headless=True, ui_app_mode=False)
        try:
            ui = await service.session('harness_ui').new_page()
            await ui.set_content('<textarea>Retained draft</textarea>')
            web = service.session('web')
            blank = await web.current_page()
            preview = await service.open_web(new_tab=True)
            await preview.set_content('<p>Preview</p>')
            result = await asyncio.wait_for(service.close_web_tab(-1), 3)
            assert result['remaining_tabs'] == 1
            assert preview.is_closed() and not blank.is_closed()
            assert await ui.locator('textarea').input_value() == 'Retained draft'
            result = await asyncio.wait_for(service.close_web_tab(0), 3)
            assert result['remaining_tabs'] == 0
            assert await ui.locator('textarea').input_value() == 'Retained draft'
            reopened = await service.open_web(new_tab=True)
            assert not reopened.is_closed()
        finally:
            await service.stop()
    asyncio.run(asyncio.wait_for(run(), 15))


def test_native_window_close_preserves_clean_profile_and_independent_ui(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    if not all(shutil.which(command) for command in ('xvfb-run', 'xdotool')):
        pytest.skip('native window-close contract requires Xvfb and xdotool')
    fixture = Path(__file__).parents[1] / 'fixtures/native_browser_close.py'
    result = subprocess.run(
        ['xvfb-run', '-a', sys.executable, str(fixture), str(tmp_path)],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
