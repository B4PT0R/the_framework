"""Opt-in real Chromium/server recovery: no inference or personal data."""

import asyncio
import os
import signal
import shutil
from pathlib import Path

import pytest

from the_framework.agent.context.session import Session
from the_framework.agent.models.responses import Message
from starter import desktop


@pytest.mark.skipif(not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                    reason="requires an explicitly configured Playwright browser installation")
def test_starter_preserves_browser_and_conversation_across_server_restart(tmp_path, monkeypatch):
    session = Session.open(tmp_path / "session.json")
    session.append(Message(role="user", content=[{"type": "input_text", "text": "Recovery marker"}]))
    for index in range(30):
        session.append(Message(role="user", content=[{"type": "input_text", "text": f"History item {index}"}]))
    session.save()
    source = tmp_path / "ui-source"
    original_ui = Path(__file__).resolve().parents[2] / "starter/ui"
    shutil.copytree(original_ui, source, ignore=shutil.ignore_patterns("node_modules", "dist"))
    (source / "node_modules").symlink_to(original_ui / "node_modules", target_is_directory=True)
    monkeypatch.setenv("STARTER_TEST_UI", str(source))
    browsers = []
    original_browser = desktop.BrowserService
    original_spawn = asyncio.create_subprocess_exec

    def browser(*args, **kwargs):
        service = original_browser(*args, **kwargs)
        browsers.append(service)
        return service

    async def spawn(*args, **kwargs):
        if args[1:3] != ("-m", "starter"):
            return await original_spawn(*args, **kwargs)
        fixture = Path(__file__).parent / "fixtures/starter_restart_server.py"
        return await original_spawn(args[0], str(fixture), *args[3:], **kwargs)

    monkeypatch.setattr(desktop, "BrowserService", browser)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def check():
        handlers = {}
        monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler",
                            lambda sig, callback: handlers.__setitem__(sig, callback))
        task = asyncio.create_task(desktop.desktop(tmp_path, headless=True))
        try:
            async with asyncio.timeout(40):
                while not browsers or not browsers[0].session("harness_ui").started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.1)
                ui = browsers[0].session("harness_ui")
                page = await ui.current_page()
                await page.get_by_text("History item 29", exact=True).wait_for()
                assert await page.get_by_text("Recovery marker", exact=True).count() == 0
                await page.get_by_role("button", name="Earlier messages").click()
                await page.get_by_text("Recovery marker", exact=True).wait_for()
                await page.screenshot(path=str(tmp_path / "history.png"))
                await page.evaluate("window.recoveryMarker = 'same page'")
                result = await page.request.post(page.url.split('/ui/')[0] + '/test/restart')
                assert result.ok, await result.text()
                old = (await result.json())["pid"]
                origin = page.url.split('/ui/')[0]
                while True:
                    try:
                        response = await page.request.get(origin + '/test/process', timeout=1000)
                        if response.ok and (await response.json())["pid"] != old:
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
                await page.get_by_text("Connected", exact=True).wait_for()
                await page.get_by_text("Ready when you are", exact=True).wait_for()
                assert await page.evaluate("window.recoveryMarker") == "same page"
                assert await page.get_by_text("Recovery marker", exact=True).count() == 1
                restored = await page.request.get(origin + '/api/v1/session')
                assert "History item 29" in await restored.text()
                assert not browsers[0].session("web").started
                await page.locator('input[type="file"]').set_input_files({
                    "name": "project-notes.txt", "mimeType": "text/plain", "buffer": b"Notes",
                })
                await page.get_by_role("button", name="Remove project-notes.txt").wait_for()
                await page.screenshot(path=str(tmp_path / "attachments.png"))
                await page.get_by_role("button", name="Remove project-notes.txt").click()
                assert await page.get_by_role("button", name="Send", exact=True).is_disabled()
                await page.get_by_role("button", name="Settings", exact=True).click()
                model = page.get_by_label("Model", exact=True)
                await model.wait_for()
                chat = page.locator(".plugin-row").filter(has_text="chat")
                await chat.get_by_text("Server only · Loaded", exact=True).wait_for()
                assert await chat.locator('input[type="checkbox"]').count() == 0
                assert await page.locator('.plugin-row input[type="checkbox"]').count() > 0
                await page.get_by_text("Advanced configuration", exact=True).click()
                draft = page.get_by_label("Agent configuration JSON")
                saved = await draft.input_value()
                for invalid in ('null', '[]', '{"model": 42}'):
                    await draft.fill(invalid)
                    assert await page.get_by_role("button", name="Save configuration", exact=True).is_disabled()
                await draft.fill(saved)
                await page.get_by_text("Advanced configuration", exact=True).click()
                await model.fill("starter-test-model")
                await page.screenshot(path=str(tmp_path / "settings.png"))
                await page.get_by_role("button", name="Save configuration", exact=True).click()
                await page.locator("aside.settings").wait_for(state="hidden")
                await page.get_by_role("button", name="Settings", exact=True).click()
                await page.wait_for_function("document.querySelector('.setting-field input')?.value === 'starter-test-model'")
                # Build a visible edit in an isolated source tree; the live UI
                # must not change before publication.
                main = source / "src/main.jsx"
                main.write_text(main.read_text().replace("<strong>Pandora</strong>", "<strong>Preview Agent</strong>"))
                built = await page.request.post(origin + '/api/v1/surfaces/main/build')
                assert built.ok, await built.text()
                candidate = await built.json()
                release = candidate["release"]
                preview = await browsers[0].open_web(origin + candidate["preview"])
                await preview.get_by_text("Preview Agent", exact=True).wait_for()
                assert await page.get_by_text("Pandora", exact=True).count() == 1
                published = await page.request.post(origin + '/api/v1/surfaces/main/publish', data={"release": release})
                assert published.ok, await published.text()
                await page.get_by_text("Preview Agent", exact=True).wait_for()
                await page.get_by_text("History item 29", exact=True).wait_for()
                rollback = await page.request.post(origin + '/api/v1/surfaces/main/rollback')
                assert rollback.ok, await rollback.text()
                await page.get_by_text("Pandora", exact=True).wait_for()
                await page.get_by_text("History item 29", exact=True).wait_for()
                await page.get_by_text("Ready when you are", exact=True).wait_for()
                await page.evaluate("() => { navigator.mediaDevices.getUserMedia = async () => { throw new Error('Test microphone denied'); }; }")
                await page.get_by_role("button", name="Voice", exact=True).click()
                await page.get_by_role("alert").filter(has_text="Test microphone denied").wait_for()
                await page.get_by_role("button", name="Voice", exact=True).wait_for()
                await page.screenshot(path=str(tmp_path / "voice.png"))
        finally:
            handlers[signal.SIGTERM]()
            await asyncio.wait_for(task, 20)

    asyncio.run(check())
