"""
Playwright page fetching
========================

Turns a URL into a `RenderedPage` (final URL + HTML + visible text). Nothing in
here knows what a product is — that is `parsing.py`'s job.

Why a real browser: most modern stores render prices, stock and variants
client-side, so `httpx.get()` returns an empty shell. Playwright waits for the
JS to run, then hands over the DOM as it actually looks.
"""

import asyncio
import json
import re
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from .config import (
    ACCEPT_LANGUAGE,
    BROWSER_CHANNEL,
    HEADLESS,
    PAGE_TIMEOUT_MS,
    SETTLE_MS,
    TARGET_LOCALE,
    TARGET_TIMEZONE,
    USER_AGENT,
    PageLoadError,
)

# Injected before any site script runs. Cheap fingerprint cleanup: removes the
# obvious "I am a bot" tells that basic anti-bot scripts check for. It is not a
# defence against serious protection (that's what the Apify routes are for).
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => __LANGUAGES__});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
const _query = window.navigator.permissions?.query;
if (_query) {
  window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _query(p);
}
"""

# Text that means "you have been blocked", not "here is a product".
_BLOCK_MARKERS = (
    "captcha", "are you a human", "robot check", "access denied",
    "unusual traffic", "verify you are a human", "enable javascript and cookies",
    "request blocked", "security check", "/login.html",
)


@dataclass
class RenderedPage:
    url: str            # URL after redirects
    html: str
    text: str           # visible body text, whitespace-collapsed
    title: str
    status: int | None = None

    def looks_blocked(self) -> bool:
        """True when the response is an anti-bot interstitial rather than the
        product page — the signal to fall back to an Apify actor."""
        if self.status is not None and self.status >= 400:
            return True
        # Bounced to a sign-in wall (Temu does this to stateless browsers).
        if re.search(r"/(login|signin|sign-in|captcha|blocked)\b", self.url, re.I):
            return True
        sample = f"{self.title}\n{self.text[:1500]}".lower()
        if any(marker in sample for marker in _BLOCK_MARKERS):
            return True
        return len(self.text.strip()) < 200


class PageFetcher:
    """Async context manager owning one browser for one or more fetches."""

    def __init__(self, *, headless: bool = HEADLESS, channel: str | None = BROWSER_CHANNEL,
                 stealth: bool = True, timeout_ms: int = PAGE_TIMEOUT_MS,
                 settle_ms: int = SETTLE_MS, locale: str = TARGET_LOCALE,
                 timezone: str = TARGET_TIMEZONE,
                 accept_language: str = ACCEPT_LANGUAGE):
        self.headless = headless
        self.channel = channel
        self.stealth = stealth
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self.locale = locale
        self.timezone = timezone
        self.accept_language = accept_language
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> "PageFetcher":
        self._playwright = await async_playwright().start()
        launch: dict = {
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if self.channel:
            launch["channel"] = self.channel
        try:
            self._browser = await self._playwright.chromium.launch(**launch)
        except PlaywrightError as exc:
            await self._playwright.stop()
            raise PageLoadError(
                f"Could not launch Chromium ({exc}). Run: playwright install chromium"
            ) from exc
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch(self, url: str) -> RenderedPage:
        # Locale/timezone/Accept-Language are set to the shopper's country so
        # stores that switch currency on browser hints (rather than IP alone)
        # show the same prices the user sees.
        context = await self._browser.new_context(
            user_agent=USER_AGENT,
            locale=self.locale,
            timezone_id=self.timezone,
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            extra_http_headers={"Accept-Language": self.accept_language},
        )
        if self.stealth:
            # navigator.languages must agree with the Accept-Language header —
            # a mismatch is itself a bot signal.
            languages = [part.split(";")[0].strip()
                         for part in self.accept_language.split(",")][:4]
            await context.add_init_script(
                _STEALTH_JS.replace("__LANGUAGES__", json.dumps(languages))
            )
        page = await context.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            try:
                response = await page.goto(url, wait_until="networkidle",
                                           timeout=self.timeout_ms)
            except PlaywrightTimeout:
                # networkidle never settles on pages with polling/analytics —
                # the DOM is usually complete anyway, so accept what we have.
                response = await page.goto(url, wait_until="domcontentloaded",
                                           timeout=self.timeout_ms)

            # Nudge lazy-loaded galleries/specs into the DOM, then let the JS
            # finish painting prices and stock badges.
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(self.settle_ms / 1000)
            await page.evaluate("window.scrollTo(0, 0)")

            html = await page.content()
            try:
                text = await page.inner_text("body", timeout=5000)
            except PlaywrightError:
                text = ""
            return RenderedPage(
                url=page.url,
                html=html,
                text=text,
                title=await page.title(),
                status=response.status if response else None,
            )
        except PlaywrightTimeout as exc:
            raise PageLoadError(f"Timed out loading {url}") from exc
        except PlaywrightError as exc:
            raise PageLoadError(f"Could not load {url}: {exc}") from exc
        finally:
            await context.close()


async def fetch_page(url: str, **options) -> RenderedPage:
    """Convenience wrapper for a single URL."""
    async with PageFetcher(**options) as fetcher:
        return await fetcher.fetch(url)
