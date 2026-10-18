"""Isolated headed Chromium; native Ctrl+Shift+W closes its last window."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from PIL import ImageGrab

from the_framework.server.clients.browser import BrowserService


async def main(root):
    service = BrowserService(root, ui_headless=True)
    web = service.session("web")
    # The desktop's Wayland environment must not escape the isolated X server.
    web.extra_args = ["--ozone-platform=x11"]
    try:
        ui = await service.session("harness_ui").new_page()
        await ui.set_content("<textarea>Preserved draft</textarea>")
        await web.start()
        await web.context.add_cookies([{
            "name": "retained", "value": "yes", "url": "https://close.test",
            "expires": time.time() + 3600,
        }])
        await web.stop()
        for index in range(3):
            page = await web.current_page()
            title = f"NativeCloseContract-{os.getpid()}-{index}"
            await page.goto(f"data:text/html,<title>{title}</title><p>Native closure test</p>")
            await page.bring_to_front()
            # Native window input, rather than a Playwright context.close().
            async with web.context.expect_event("close", timeout=10000):
                process = await asyncio.create_subprocess_exec(
                    "xdotool", "search", "--sync", "--onlyvisible", "--name", title,
                    "windowfocus", "--sync", "key", "--clearmodifiers", "ctrl+shift+w",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(process.communicate(), 10)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                # X may reject the final key-up after the window disappears;
                # the observed context close is the authoritative result.
            await web.stop()
            preferences = json.loads((web.profile_dir / "Default/Preferences").read_text())
            assert preferences["profile"]["exit_type"] == "Normal"
            assert await ui.locator("textarea").input_value() == "Preserved draft"
            await web.start()
            cookies = await web.context.cookies("https://close.test")
            assert any(cookie["name"] == "retained" and cookie["value"] == "yes" for cookie in cookies)
            if index == 2:
                reopened = await web.current_page()
                await reopened.goto("data:text/html,<title>Reopened cleanly</title><h1>Browser reopened</h1>")
                await reopened.bring_to_front()
                await asyncio.sleep(0.5)  # Let the native window paint before capture.
                ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(root / "reopened-native.png")
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
