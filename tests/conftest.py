"""Browser tests use their own runtime, never the running application's cache."""
import os
from importlib.metadata import version
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--browser-cache", help="Explicit Playwright browser cache (default: Playwright user cache)")


def pytest_configure(config):
    cache = config.getoption("--browser-cache")
    if cache:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(cache).expanduser().resolve())
    else:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)


def pytest_report_header(config):
    return f"Playwright {version('playwright')}; browser cache: {os.environ.get('PLAYWRIGHT_BROWSERS_PATH', 'standard user cache')}"


@pytest.fixture(scope="session")
def browser_runtime(pytestconfig):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        expected = Path(playwright.chromium.executable_path)
        if not expected.is_file():
            message = (f"Playwright {version('playwright')} needs {expected}. "
                       "Prepare explicitly with this environment's python -m playwright install chromium; "
                       "tests never download browsers or select another revision.")
            if pytestconfig.getoption("--browser-cache"):
                pytest.fail(message)
            pytest.skip(message)
        # Check the headless shell and OS dependencies too, before running contracts.
        browser = playwright.chromium.launch()
        browser.close()

