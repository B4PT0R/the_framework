"""Opt-in WebRTC/backend smoke test; fake microphone, isolated app and data."""

import asyncio
import os
import socket

import pytest
import uvicorn
from playwright.async_api import async_playwright

from starter.server import create_app


@pytest.mark.skipif(os.environ.get("STARTER_LIVE_TESTS") != "1",
                    reason="creates a real backend voice call using account quota")
def test_starter_voice_audio_transcript_and_cleanup(tmp_path):
    async def check():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            app = create_app(tmp_path, token="voice-test", origin=origin)
            server = uvicorn.Server(uvicorn.Config(app, access_log=False))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(90):
                    while not server.started:
                        if task.done():
                            await task
                        await asyncio.sleep(0.1)
                    async with async_playwright() as playwright:
                        browser = await playwright.chromium.launch(headless=True, args=[
                            "--use-fake-device-for-media-stream",
                            "--use-fake-ui-for-media-stream",
                        ])
                        try:
                            context = await browser.new_context(permissions=["microphone"])
                            await context.add_cookies([{
                                "name": "starter_session", "value": "voice-test", "url": origin,
                            }])
                            await context.add_init_script("""
                                const Peer = window.RTCPeerConnection;
                                window.RTCPeerConnection = class extends Peer {
                                  constructor(...args) { super(...args); window.testPeer = this; }
                                };
                            """)
                            page = await context.new_page()
                            await page.goto(origin + "/ui/")
                            await page.get_by_text("Connected", exact=True).wait_for()
                            await page.get_by_role("button", name="Voice", exact=True).click()
                            await page.wait_for_function("""() =>
                                [...document.querySelectorAll('button')].some(b => b.textContent === 'End voice')
                                || document.querySelector('[role=alert]')
                            """)
                            assert not await page.get_by_role("alert").count(), await page.get_by_role("alert").all_text_contents()
                            await page.evaluate("""() => {
                                window.sawLiveCaption = false;
                                new MutationObserver(() => {
                                    if ([...document.querySelectorAll('.message small')]
                                        .some(node => node.textContent.includes('· Live')))
                                        window.sawLiveCaption = true;
                                }).observe(document.body, {subtree: true, childList: true, characterData: true});
                            }""")
                            await page.get_by_label("Message", exact=True).fill("Say only: voice ready.")
                            await page.get_by_role("button", name="Send", exact=True).click()
                            await page.wait_for_function("""async () => {
                                const stats = await window.testPeer.getStats();
                                return [...stats.values()].some(s => s.type === 'inbound-rtp'
                                  && s.kind === 'audio' && s.bytesReceived > 0);
                            }""")
                            await page.wait_for_function("""() => [...document.querySelectorAll('.message.assistant')]
                                .some(node => node.textContent.toLowerCase().includes('voice ready'))""")
                            assert await page.evaluate("window.sawLiveCaption")
                            await page.screenshot(path=str(tmp_path / "voice-response.png"))
                            await page.get_by_role("button", name="End voice", exact=True).click()
                            await page.get_by_role("button", name="Voice", exact=True).wait_for()
                            assert await page.evaluate("window.testPeer.connectionState") == "closed"
                            assert await page.get_by_text("Assistant · Live", exact=True).count() == 0
                            snapshot = await page.request.get(origin + "/api/v1/session")
                            assert "voice ready" in (await snapshot.text()).lower()
                        finally:
                            await browser.close()
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 20)

    asyncio.run(check())
