"""Real Chromium control contracts; no user profile or inference."""
import asyncio
import os

import pytest

from the_framework.server.clients.browser import BrowserService, BROWSER_AGENT_ID_ATTR

pytestmark = pytest.mark.usefixtures("browser_runtime")


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
            first = await service.open_web("data:text/html,<title>First requested tab</title>", new_tab=True)
            assert len(web.context.pages) == 1
            assert web.context.pages == [first]
            preview = await service.open_web(new_tab=True)
            await preview.set_content('<p>Preview</p>')
            result = await asyncio.wait_for(service.close_web_tab(-1), 3)
            assert result['remaining_tabs'] == 1
            assert preview.is_closed() and not first.is_closed()
            assert await ui.locator('textarea').input_value() == 'Retained draft'
            result = await asyncio.wait_for(service.close_web_tab(0), 3)
            assert result['remaining_tabs'] == 0
            assert not web.started
            assert not web._cleanup_tasks
            assert (await web.status())['open'] is False
            assert await ui.locator('textarea').input_value() == 'Retained draft'
            reopened = await service.open_web(new_tab=True)
            assert not reopened.is_closed()
            assert len(web.context.pages) == 1
            # Native/website closure takes the same empty-session path without
            # requiring the agent's close_tab tool (also in headless Chromium).
            await reopened.close()
            async with asyncio.timeout(3):
                while web.started or web._cleanup_tasks:
                    await asyncio.sleep(0.01)
            assert await ui.locator('textarea').input_value() == 'Retained draft'
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


def test_ui_downloads_keep_native_names_and_survive_browser_shutdown(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    if not shutil.which('xvfb-run'):
        pytest.skip('native download contract requires Xvfb')
    fixture = Path(__file__).parents[1] / 'fixtures/native_browser_download.py'
    result = subprocess.run(
        ['xvfb-run', '-a', sys.executable, str(fixture), str(tmp_path)],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_browser_survives_observation_closure_and_resumes_user_tabs(tmp_path):
    from the_framework.agent import Agent, Image
    from the_framework.plugins.browser import ChromiumExtension

    service = BrowserService(tmp_path, web_headless=True)
    agent = Agent(client=object())
    plugin = agent.add_extension(ChromiumExtension(agent, runtime_root=tmp_path,
        service_factory=lambda: service))
    try:
        assert asyncio.run(plugin.chromium_state()) is None
        assert plugin.open_session("web")["open"] is False
        assert not service.sessions["web"].started
        plugin.open_web("data:text/html,<title>Retained</title><p>First</p>", new_tab=True)
        assert len(plugin.status()["tabs"]) == 1
        plugin.close_session()
        assert asyncio.run(plugin.chromium_state()) is None
        assert service.sessions["web"].started
        # Simulate user navigation without opening agent observation.
        plugin.controller.call("open_web", "data:text/html,<title>User tab</title><p>New</p>", new_tab=True)
        assert asyncio.run(plugin.chromium_state()) is None
        assert len(plugin.open_session("web")["tabs"]) == 2
        observation = asyncio.run(plugin.chromium_state())
        assert isinstance(observation, tuple), observation
        text, screenshot = observation
        assert "User tab" in text
        assert isinstance(screenshot, Image)
        plugin.close_web()
        assert plugin.active_target == "web"
        assert asyncio.run(plugin.chromium_state())["open"] is False
        plugin.close_session()
        assert asyncio.run(plugin.chromium_state()) is None
    finally:
        plugin.shutdown()


def test_long_form_previews_and_paginated_reads_preserve_page_and_handles(tmp_path):
    from the_framework.plugins.browser import ChromiumExtension

    async def run():
        service = BrowserService(tmp_path, web_headless=True)
        try:
            page = await service.open_web(new_tab=True)
            await page.set_content('<textarea aria-label="Draft"></textarea><div contenteditable="true" aria-label="Editor"></div><a href="https://example.test/">Link</a>')
            value = 'Début 🐈\n' + 'long draft line\n' * 1000 + 'FIN'
            await page.locator('textarea').fill(value)
            await page.locator('[contenteditable]').fill(value)
            session = service.session('web')
            snapshot = await session.snapshot()
            preview = ChromiumExtension._format_snapshot('web', snapshot)
            assert snapshot['read_element_available'] is True
            assert 'read_element' in preview and 'FIN' in preview
            assert len(preview) < 2500
            for name in ('Draft', 'Editor'):
                element = next(e for e in snapshot['elements'] if e['name'] == name)
                assert element['value'] == value
                chunks, offset = [], 0
                while True:
                    result = await session.read_element(element['id'], offset=offset, limit=777)
                    chunks.append(result['content'])
                    assert result['total_chars'] == len(value)
                    offset = result['next_offset']
                    if offset is None:
                        break
                assert ''.join(chunks) == value
                assert await (await session.locator(element['id'])).count() == 1
            link = next(e for e in snapshot['elements'] if e['role'] == 'link')
            assert (await session.read_element(link['id'], field='href'))['content'] == 'https://example.test/'
            assert (await session.read_element(link['id'], field='text'))['content'] == 'Link'
            assert (await session.read_element(link['id'], offset=500))['content'] == ''
            for kwargs in ({'field': 'bad'}, {'limit': 0}, {'limit': 16001}, {'offset': -1}, {'limit': True}):
                with pytest.raises(Exception, match='field must|offset must'):
                    await session.read_element(link['id'], **kwargs)
            await session.fill(next(e['id'] for e in snapshot['elements'] if e['name'] == 'Draft'), 'Still editable')
            assert await page.locator('textarea').input_value() == 'Still editable'
        finally:
            await service.stop()
    asyncio.run(asyncio.wait_for(run(), 20))
