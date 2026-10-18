"""Exercise real desktop downloads without touching the user's profile or files."""
import asyncio
import json
from pathlib import Path
import sys

from the_framework.server.clients.browser import BrowserService


async def main(root):
    downloads = root / 'Downloads'
    downloads.mkdir()
    service = BrowserService(root, web_headless=True, ui_app_mode=False)
    session = service.session('harness_ui')
    profile = session.profile_dir / 'Default'
    profile.mkdir(parents=True)
    (profile / 'Preferences').write_text(json.dumps({
        'download': {'default_directory': str(downloads), 'prompt_for_download': False},
    }))
    try:
        page = await session.current_page()
        await page.set_content('<a download="example.txt" href="data:text/plain,native%20download">Download</a>')
        for expected in ('example.txt', 'example (1).txt'):
            await page.locator('a').click()
            async with asyncio.timeout(10):
                while not (downloads / expected).exists():
                    await asyncio.sleep(.05)
            assert (downloads / expected).read_text() == 'native download'
        assert not service.session('web').native_downloads
    finally:
        await service.stop()
    assert sorted(p.name for p in downloads.iterdir()) == ['example (1).txt', 'example.txt']
    assert all(p.read_text() == 'native download' for p in downloads.iterdir())


asyncio.run(asyncio.wait_for(main(Path(sys.argv[1])), 30))
