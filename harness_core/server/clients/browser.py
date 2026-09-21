"""Persistent Playwright runtime with isolated web and application UI targets."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from modict import modict

DEFAULT_VIEWPORT = {"width": 1365, "height": 900}
BROWSER_AGENT_ID_ATTR = "data-harness-browser-id"
BROWSER_TARGETS = {"web", "harness_ui"}


class BrowserError(RuntimeError):
    """Raised when the Playwright browser runtime cannot satisfy a request."""


SNAPSHOT_SCRIPT = r"""
({ idAttr, maxElements, maxTextLength }) => {
  const tags = new Set(['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'OPTION', 'SUMMARY', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6']);
  const roles = new Set(['button', 'link', 'checkbox', 'radio', 'textbox', 'searchbox', 'combobox', 'listbox', 'option', 'tab', 'menuitem', 'switch', 'slider', 'spinbutton']);
  const clean = text => (text || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const clickable = el => !!(el.onclick || el.closest('a,button,[role="button"],[role="link"],[onclick]'));
  const roleFor = el => {
    const explicit = clean(el.getAttribute('role')).toLowerCase();
    if (explicit) return explicit;
    const type = clean(el.getAttribute('type')).toLowerCase();
    if (el.tagName === 'A') return 'link';
    if (el.tagName === 'BUTTON') return 'button';
    if (el.tagName === 'TEXTAREA') return 'textarea';
    if (el.tagName === 'SELECT') return 'select';
    if (el.tagName === 'INPUT') return ['checkbox', 'radio', 'range'].includes(type) ? type : (type || 'input');
    if (/^H[1-6]$/.test(el.tagName)) return 'heading';
    if (clickable(el)) return 'clickable';
    return el.tagName.toLowerCase();
  };
  const nameFor = el => {
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    const labelledBy = clean(el.getAttribute('aria-labelledby'));
    if (labelledBy) {
      const text = labelledBy.split(/\s+/).map(id => clean(document.getElementById(id)?.innerText)).filter(Boolean).join(' ');
      if (text) return text;
    }
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return clean(el.getAttribute('placeholder') || el.getAttribute('name') || el.value);
    if (el.tagName === 'SELECT') return clean(el.getAttribute('name') || el.selectedOptions?.[0]?.innerText || el.innerText);
    if (el.tagName === 'A') return clean(el.innerText || el.getAttribute('title') || el.getAttribute('href'));
    return clean(el.innerText || el.getAttribute('title') || el.getAttribute('name') || el.value);
  };
  const interesting = el => roles.has(roleFor(el)) || tags.has(el.tagName) || el.hasAttribute('contenteditable') || (el.hasAttribute('tabindex') && Number(el.getAttribute('tabindex')) >= 0) || clickable(el);
  const elements = [];
  let nextId = 1;
  for (const el of Array.from(document.querySelectorAll('body *'))) {
    if (elements.length >= maxElements) break;
    if (!visible(el) || !interesting(el)) continue;
    const role = roleFor(el);
    const name = nameFor(el).slice(0, maxTextLength);
    if (!name && !['input', 'textarea', 'select', 'checkbox', 'radio', 'button'].includes(role)) continue;
    const id = String(nextId++);
    el.setAttribute(idAttr, id);
    const rect = el.getBoundingClientRect();
    elements.push({
      id, role, name, tag: el.tagName.toLowerCase(),
      text: clean(el.innerText).slice(0, maxTextLength),
      href: el.href || null, type: el.getAttribute('type') || null,
      value: el.value || null, placeholder: el.getAttribute('placeholder') || null,
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      checked: !!el.checked,
      rect: {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)}
    });
  }
  return {url: location.href, title: document.title, elements, truncated: elements.length >= maxElements};
}
"""


def _default_playwright_factory():
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:  # pragma: no cover - depends on deployment
        raise BrowserError(
            "Playwright is unavailable; install the Python package and its Chromium runtime"
        ) from error
    return async_playwright()


def validate_target(target: str) -> str:
    if target not in BROWSER_TARGETS:
        raise BrowserError(f"unknown browser target: {target}")
    return target


def validate_harness_ui_url(url: str, allowed_origins=()) -> str:
    parsed = urlparse(str(url))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme not in {"http", "https"} or not (
        loopback or parsed.scheme == "https" and origin in set(allowed_origins)
    ):
        raise BrowserError("Harness UI navigation is restricted to loopback or configured trusted origins")
    return str(url)


def is_harness_ui_resource(url: str, allowed_origins=()) -> bool:
    parsed = urlparse(str(url))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return parsed.scheme in {"about", "blob", "data"} or (
        parsed.scheme in {"http", "https"}
        and (
            parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            or parsed.scheme == "https" and origin in set(allowed_origins)
        )
    )


class BrowserSession(modict):
    _config = modict.config(strict=True, extra="forbid", auto_convert=False)

    profile_dir: Path
    headless: bool = False
    viewport: dict[str, int] | None = modict.factory(lambda: dict(DEFAULT_VIEWPORT))
    no_viewport: bool = False
    extra_args: list[str] = modict.factory(list)
    ignore_default_args: list[str] = modict.factory(list)
    playwright_factory: Callable[[], Any] = _default_playwright_factory
    _manager = modict.attr(None)
    _playwright = modict.attr(None)
    _context = modict.attr(None)
    _active_page = modict.attr(None)
    initial_cookies = modict.attr(None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_attr("_cleanup_tasks", set())

    @property
    def started(self):
        return self._context is not None

    @property
    def context(self):
        if self._context is None:
            raise BrowserError("browser target is not started")
        return self._context

    async def start(self):
        if self.started:
            return self
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._manager = modict.attr(self.playwright_factory())
        self._playwright = modict.attr(await self._manager.start())
        self._context = modict.attr(await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport=self.viewport,
            no_viewport=self.no_viewport,
            args=list(self.extra_args),
            ignore_default_args=list(self.ignore_default_args),
        ))
        if self.initial_cookies:
            await self._context.add_cookies(self.initial_cookies)
        if hasattr(self._context, "on"):
            # Playwright caches bound-method wrappers on their owner. Keep its
            # private attributes off this strict payload model.
            self._context.on("close", lambda *_args: self._closed())
        return self

    def _closed(self, *_args):
        playwright = self._playwright
        self._context = modict.attr(None)
        self._playwright = modict.attr(None)
        self._manager = modict.attr(None)
        self._active_page = modict.attr(None)
        if playwright is None:
            return
        # Persistent contexts may be closed by Chromium itself. Playwright does
        # not automatically stop the per-session driver in that case, and each
        # subsequent launch would otherwise leak another resident Node process.
        task = asyncio.create_task(playwright.stop())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def stop(self):
        context, playwright = self._context, self._playwright
        self._context = self._playwright = self._manager = self._active_page = modict.attr(None)
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
        if self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)

    async def pages(self):
        await self.start()
        return list(getattr(self.context, "pages", []))

    async def new_page(self):
        await self.start()
        self._active_page = modict.attr(await self.context.new_page())
        return self._active_page

    async def current_page(self):
        pages = await self.pages()
        if self._active_page in pages:
            return self._active_page
        focused = None
        visible = None
        for page in pages:
            try:
                state = await page.evaluate(
                    "() => ({visibilityState: document.visibilityState, hasFocus: document.hasFocus()})"
                )
            except Exception:
                continue
            if state.get("visibilityState") == "visible":
                visible = page
            if state.get("hasFocus"):
                focused = page
        self._active_page = modict.attr(focused or visible or (pages[-1] if pages else None))
        return self._active_page or await self.new_page()

    async def select_tab(self, index: int):
        pages = await self.pages()
        if index < 0:
            index += len(pages)
        if not 0 <= index < len(pages):
            raise BrowserError(f"tab index out of range: {index}")
        page = pages[index]
        await page.bring_to_front()
        self._active_page = modict.attr(page)
        return page

    async def close_tab(self, index=None):
        if not self.started:
            raise BrowserError("browser target is not started")
        pages = list(getattr(self.context, "pages", []))
        if not pages:
            raise BrowserError("browser target has no open tabs")
        if index is None:
            page = await self.current_page()
            index = pages.index(page)
        else:
            index = int(index)
            if index < 0:
                index += len(pages)
            if not 0 <= index < len(pages):
                raise BrowserError(f"tab index out of range: {index}")
            page = pages[index]
        await page.close()
        if self._active_page is page:
            self._active_page = modict.attr(None)
        remaining = list(getattr(self.context, "pages", [])) if self.started else []
        if remaining:
            self._active_page = modict.attr(remaining[min(index, len(remaining) - 1)])
            await self._active_page.bring_to_front()
        return {"closed_index": index, "remaining_tabs": len(remaining)}

    async def status(self):
        pages = await self.pages() if self.started else []
        active = await self.current_page() if pages else None
        tabs = []
        for index, page in enumerate(pages):
            try:
                title = await page.title()
            except Exception:
                title = ""
            tabs.append({
                "index": index,
                "active": page is active,
                "title": title,
                "url": getattr(page, "url", "") or "",
            })
        return {"open": self.started, "profile_dir": str(self.profile_dir), "tabs": tabs}

    async def snapshot(self, *, max_elements=120, max_text_length=140):
        page = await self.current_page()
        return await page.evaluate(SNAPSHOT_SCRIPT, {
            "idAttr": BROWSER_AGENT_ID_ATTR,
            "maxElements": max_elements,
            "maxTextLength": max_text_length,
        })

    async def screenshot(self, path: Path, *, full_page=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        await (await self.current_page()).screenshot(path=str(path), full_page=full_page)
        return path

    async def goto(self, url: str):
        page = await self.current_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self._active_page = modict.attr(page)
        return page

    async def back(self):
        page = await self.current_page()
        await page.go_back(wait_until="domcontentloaded", timeout=30_000)
        return page

    async def locator(self, element_id):
        page = await self.current_page()
        return page.locator(f'[{BROWSER_AGENT_ID_ATTR}="{element_id}"]')

    async def click(self, element_id):
        await (await self.locator(element_id)).click()

    async def fill(self, element_id, text):
        await (await self.locator(element_id)).fill(text)

    async def select(self, element_id, value):
        return await (await self.locator(element_id)).select_option(value)

    async def press(self, key, element_id=None):
        if element_id is not None:
            await (await self.locator(element_id)).press(key)
            return
        await (await self.current_page()).keyboard.press(key)


class BrowserService:
    """Own two physically separate persistent Chromium contexts."""

    def __init__(
        self,
        runtime_root,
        *,
        web_headless=False,
        ui_headless=False,
        ui_app_mode=True,
        viewport=None,
        playwright_factory=None,
        harness_ui_origins=(),
        initial_cookies=None,
    ):
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.screenshot_root = self.runtime_root / "browser" / "screenshots"
        self._ui_routed_context = None
        # Headless Chromium has no application window; suppressing its initial
        # page can otherwise leave persistent-context startup waiting forever.
        self.ui_app_mode = ui_app_mode and not ui_headless
        self.harness_ui_origins = frozenset(harness_ui_origins)
        factory = playwright_factory or _default_playwright_factory
        viewport = dict(viewport or DEFAULT_VIEWPORT)
        self.sessions = {
            "web": BrowserSession(
                profile_dir=self.runtime_root / "browser" / "profiles" / "web",
                headless=web_headless,
                viewport=dict(viewport),
                playwright_factory=factory,
            ),
            "harness_ui": BrowserSession(
                profile_dir=self.runtime_root / "browser" / "profiles" / "harness_ui",
                headless=ui_headless,
                viewport=None,
                no_viewport=True,
                ignore_default_args=["about:blank"] if self.ui_app_mode else [],
                playwright_factory=factory,
            ),
        }
        for session in self.sessions.values():
            session.set_attr("initial_cookies", initial_cookies)

    def session(self, target):
        return self.sessions[validate_target(target)]

    async def stop(self):
        await asyncio.gather(*(session.stop() for session in self.sessions.values()))

    async def open_web(self, url=None, *, new_tab=False):
        session = self.session("web")
        page = await session.new_page() if new_tab else await session.current_page()
        if url:
            await session.goto(url)
        return page

    async def open_harness_ui(self, url):
        url = validate_harness_ui_url(url, self.harness_ui_origins)
        session = self.session("harness_ui")
        if self.ui_app_mode and not session.started:
            parsed = urlparse(url)
            app_path = "/remote-ui/" if parsed.path.startswith("/remote-ui") else "/ui/"
            app_url = f"{parsed.scheme}://{parsed.netloc}{app_path}"
            session.extra_args = [
                f"--app={app_url}",
                "--start-maximized",
            ]
            if parsed.scheme == "https" and parsed.hostname not in {
                "127.0.0.1", "localhost", "::1",
            }:
                session.extra_args.append("--enable-features=VaapiVideoDecoder")
        pages = await session.pages()
        if hasattr(session.context, "route") and self._ui_routed_context is not session.context:
            async def restrict(route, request):
                if is_harness_ui_resource(request.url, self.harness_ui_origins):
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")

            await session.context.route("**/*", restrict)
            self._ui_routed_context = session.context
        page = pages[0] if pages else await session.new_page()
        session._active_page = modict.attr(page)
        await session.goto(url)
        await page.bring_to_front()
        return page

    async def close_web_tab(self, index=None):
        """Close one web tab without exposing the Harness UI session."""
        return await self.session("web").close_tab(index)

    async def close_web(self):
        """Close only the arbitrary web navigation session."""
        await self.session("web").stop()
        return {"closed": True}

    async def goto(self, target, url):
        if validate_target(target) != "web":
            raise BrowserError("the Harness UI target cannot be navigated by an agent tool")
        return await self.session(target).goto(url)

    async def observe(self, target, screenshot_path, *, max_elements=120):
        session = self.session(target)
        snapshot = await session.snapshot(max_elements=max_elements)
        path = await session.screenshot(Path(screenshot_path), full_page=False)
        return snapshot, path


class BrowserController:
    """Synchronous owner facade backed by one dedicated asyncio loop thread."""

    def __init__(self, service_factory):
        self.service_factory = service_factory
        self._loop = None
        self._thread = None
        self._service = None
        self._lock = threading.Lock()

    def _ensure_loop(self):
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return
            ready = threading.Event()

            def run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()
                loop.close()

            self._thread = threading.Thread(target=run, daemon=True, name="harness-playwright")
            self._thread.start()
            ready.wait()

    def _run(self, operation):
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(operation(), self._loop)
        try:
            return future.result()
        except concurrent.futures.CancelledError as error:
            raise BrowserError("browser operation was cancelled") from error

    def service(self):
        if self._service is None:
            self._service = self.service_factory()
        return self._service

    def call(self, method, *args, **kwargs):
        return self._run(lambda: getattr(self.service(), method)(*args, **kwargs))

    def session_call(self, target, method, *args, **kwargs):
        return self._run(
            lambda: getattr(self.service().session(target), method)(*args, **kwargs)
        )

    def close(self):
        loop, thread = self._loop, self._thread
        if loop is None:
            return
        try:
            if self._service is not None:
                self._run(self._service.stop)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not threading.current_thread():
                thread.join(timeout=5)
            self._loop = self._thread = self._service = None
