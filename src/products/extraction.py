"""Invocation of the product-extraction agent (``src/agents/product_extraction``).

The **only** module that imports the agent. It owns everything that surrounds the call --
the concurrency bound, the global timeout, the per-request region, and the translation of
the agent's exceptions into HTTP errors -- so ``service.py`` only has to ask for a product
sheet and store it, and the router never sees an agent exception.

Import ordering
---------------
The agent's ``config.py`` calls ``load_dotenv(override=True)`` at import time: importing it
pushes every value of ``.env`` into ``os.environ``, *overwriting* variables genuinely
injected by the environment (a CI job exporting ``DATABASE_URL`` while a stale ``.env``
sits on disk). It also freezes its own settings (``PRODUCT_COUNTRY``, ``PRODUCT_VARIANTS``,
``OPENAI_MODEL``) at that same moment.

``src/products/__init__.py`` therefore builds every project ``BaseSettings`` before any
submodule of this package runs -- see its docstring. By the time ``load_dotenv`` fires,
the application configuration is already fixed, whatever the agent does to the environment
afterwards.

Per-request region
------------------
The agent freezes ``PRODUCT_COUNTRY`` at import time, so one region per request is not
supported natively. Two facts decide how it is handled, without touching the agent:

1. The **Playwright path is already per-request**: ``locale``, ``timezone`` and
   ``accept_language`` travel from ``extract_product_data(**options)`` down to
   ``PageFetcher``, where they are ordinary parameters and not module constants.
2. The **Apify path is not**: the adapters read ``TARGET_COUNTRY`` from their module
   globals. Rather than mutating that global under a lock -- which would serialize every
   extraction, and which forcing ``os.environ`` would not even achieve since the constant
   is read once at import -- one adapter clone per (actor, allowed region) is registered
   through the agent's own ``register_adapter()`` extension point. Each clone closes over
   its region, so nothing mutable is shared and extractions stay parallel.

Everything region-dependent goes through :func:`_apply_region`, the single place the
shopper country is applied.

Known residue, by design: the country string interpolated in the agent's LLM prompt and
``ProductData.country`` stay pinned to ``PRODUCT_COUNTRY`` -- neither is exposed per call
by the agent. Both are descriptive only, and neither is reached with ``use_agent=False``.
"""

import asyncio
import logging
import os.path
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace as replace_dataclass
from typing import Any

from src.agents.product_extraction import (
    ACTOR_ADAPTERS,
    ActorRunError,
    ConfigError,
    ExtractionError,
    PageLoadError,
    PlatformUnsupportedError,
    ProductSummary,
    UnsupportedUrlError,
    detect_route,
    extract_product_data,
    register_adapter,
    summarize,
)
from src.products.config import products_settings
from src.products.constants import (
    ACTOR_COUNTRY_KEYS,
    NEUTRAL_REGION_PROFILE,
    REGION_ACTOR_SEPARATOR,
    REGION_PROFILES,
)
from src.products.exceptions import (
    ExtractionFailed,
    ExtractionNotConfigured,
    ExtractionTimedOut,
    PlatformNotExtractable,
    ProductPageLoadFailed,
    ScraperRunFailed,
    UnsupportedProductUrl,
)

logger = logging.getLogger(__name__)

# One Chromium (~300 MB) per concurrent extraction, so this caps memory as much as load.
_semaphore = asyncio.Semaphore(products_settings.EXTRACTION_MAX_CONCURRENCY)

# Playwright starts its Node driver with `asyncio.create_subprocess_exec`, which on Windows
# only works on a ProactorEventLoop. uvicorn picks the loop from
# `use_subprocess = reload or workers > 1` (uvicorn/loops/asyncio.py) and hands a
# SelectorEventLoop to the worker in exactly that case -- so under `uvicorn --reload` or
# `--workers 2`, every browser-rendered extraction would die with NotImplementedError
# before a single page is fetched. `_on_browser_capable_loop` works around it rather than
# asking the developer to give up --reload.
_SELECTOR_LOOP_NOTICE = (
    "This event loop cannot start the Playwright driver (Windows + uvicorn "
    "--reload/--workers>1 forces a SelectorEventLoop). Running the extraction on a "
    "dedicated ProactorEventLoop thread instead."
)


def _loop_can_spawn_subprocesses() -> bool:
    """Whether the running loop can start the Playwright driver process."""
    if sys.platform != "win32":
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True  # no loop yet: nothing to rule out
    return isinstance(loop, asyncio.ProactorEventLoop)


async def _on_browser_capable_loop(make_coroutine: Callable[[], Awaitable[Any]]) -> Any:
    """Await ``make_coroutine()`` on a loop that can start the Playwright driver.

    Normally -- Linux, macOS, and Windows without ``--reload`` -- this is a plain ``await``
    on the request's own loop, and nothing is added.

    Only when the running loop cannot spawn subprocesses (see above) does it fall back to a
    worker thread owning a private ``ProactorEventLoop``. This is deliberately **not** a
    "wrap the async call in a thread" shortcut: the main loop still awaits, so nothing is
    blocked, and the fallback exists because on that loop there is no other way to start a
    browser at all.

    ``make_coroutine`` is a factory rather than a coroutine so the coroutine object is
    created inside the target loop.

    Caveat: on timeout the main loop stops waiting, but the thread cannot be killed and
    runs to completion in the background. With ``EXTRACTION_MAX_CONCURRENCY`` slots held by
    the semaphore around this call, a slot is only freed once that thread really finishes.
    """
    if _loop_can_spawn_subprocesses():
        return await make_coroutine()

    logger.warning(_SELECTOR_LOOP_NOTICE)

    def run_on_private_loop() -> Any:
        loop = asyncio.ProactorEventLoop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(make_coroutine())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    return await asyncio.to_thread(run_on_private_loop)


# ---------------------------------------------------------------------------
# 1. Region -> agent options (the single arbitration point)
# ---------------------------------------------------------------------------
def _regional_build_input(base_build_input: Any, region: str) -> Any:
    """Wrap an adapter's input builder so it asks the actor for ``region``.

    Only keys the adapter *already* sends are rewritten, and their original casing is
    preserved (``junglee/Amazon-crawler`` wants ``MA``, ``apify/e-commerce-scraping-tool``
    wants ``ma``). An actor with no country input is left strictly untouched rather than
    being fed a parameter it would reject.
    """

    def build_input(url: str) -> dict[str, Any]:
        payload = base_build_input(url)
        for key in ACTOR_COUNTRY_KEYS:
            current = payload.get(key)
            if isinstance(current, str):
                payload[key] = region if current.isupper() else region.lower()
        return payload

    return build_input


def _cloner_adaptateur(actor: str, region: str) -> str | None:
    """Create the ``actor@REGION`` clone of one adapter, if it does not exist yet.

    Args:
        actor: Key of the base adapter.
        region: Shopper country, uppercase.

    Returns:
        The clone's key, or ``None`` when the base adapter is unknown.
    """
    cle = f"{actor}{REGION_ACTOR_SEPARATOR}{region}"
    if cle in ACTOR_ADAPTERS:
        return cle
    base = ACTOR_ADAPTERS.get(actor)
    if base is None:
        return None
    register_adapter(
        replace_dataclass(
            base,
            key=cle,
            label=f"{base.label} [{region}]",
            build_input=_regional_build_input(base.build_input, region),
        )
    )
    return cle


def register_region_adapters() -> None:
    """Pre-register the adapter clones for the configured region whitelist.

    A WARM-UP, NOT THE GUARANTEE. It was the guarantee, and that stopped being
    true the day the whitelist was allowed to be empty — which means "no
    restriction", so this loop had nothing to iterate and registered zero clones.
    Every Apify extraction then fell back to the agent's default country, and
    logged a "should not happen" while doing it.

    `_apply_region` now creates the clone it needs on demand, so an open
    whitelist is served just as well as a closed one. This function stays
    because pre-registering keeps the first extraction of each configured region
    off the critical path, and because it fails loudly at startup if an adapter
    cannot be cloned at all.

    Idempotent: re-registering overwrites a clone with an equivalent one.
    """
    for region in products_settings.sorted_allowed_regions:
        for key in list(ACTOR_ADAPTERS):
            if REGION_ACTOR_SEPARATOR not in key:  # skip clones already made
                _cloner_adaptateur(key, region)


def _apply_region(region: str, url: str) -> dict[str, Any]:
    """Translate a region into the options the agent must be called with.

    Returns the browser identity (``locale`` / ``timezone`` / ``accept_language``), plus
    ``force_actor`` pointing at the regional clone of the actor this URL routes to, when
    (and only when) the URL routes to Apify.
    """
    locale, timezone, accept_language = REGION_PROFILES.get(region, NEUTRAL_REGION_PROFILE)
    options: dict[str, Any] = {
        "locale": locale,
        "timezone": timezone,
        "accept_language": accept_language,
    }

    # detect_route is a pure function: no network, no side effect.
    route = detect_route(url)
    if route.strategy == "apify" and route.actor:
        # Created on demand rather than looked up: the region whitelist may be
        # open, in which case there is no finite list to pre-register from.
        regional_key = _cloner_adaptateur(route.actor, region)
        if regional_key:
            options["force_actor"] = regional_key
        else:
            logger.warning(
                "Unknown actor %s for this route; falling back to the agent's "
                "default country.",
                route.actor,
            )
    return options


# ---------------------------------------------------------------------------
# 2. Error mapping
# ---------------------------------------------------------------------------
def _http_error(exc: BaseException) -> Exception:
    """Translate a failure of the extraction agent into a domain HTTP error."""
    if isinstance(exc, TimeoutError):  # asyncio.timeout raises the builtin
        return ExtractionTimedOut()
    if isinstance(exc, NotImplementedError):
        # Safety net: a loop that refused to spawn the browser driver despite
        # `_on_browser_capable_loop`. The page was never reached, so this is a page-load
        # failure rather than a mystery 500.
        logger.error(
            "The browser driver could not be started on this event loop (%s).",
            type(asyncio.get_event_loop()).__name__,
        )
        return ProductPageLoadFailed()
    # Avant `UnsupportedUrlError`, dont c'est une sous-classe : l'ordre décide.
    if isinstance(exc, PlatformUnsupportedError):
        return PlatformNotExtractable()
    if isinstance(exc, UnsupportedUrlError):
        return UnsupportedProductUrl()
    if isinstance(exc, PageLoadError):
        return ProductPageLoadFailed()
    if isinstance(exc, ActorRunError):
        return ScraperRunFailed()
    if isinstance(exc, ConfigError):
        # Never interpolate the exception message: it names the missing credential and
        # could quote its value.
        return ExtractionNotConfigured()
    if isinstance(exc, ExtractionError):
        return ScraperRunFailed()  # remaining agent errors are upstream failures
    return ExtractionFailed()


# ---------------------------------------------------------------------------
# 3. The call
# ---------------------------------------------------------------------------
async def extract_product(
    url: str, region: str, *, use_agent: bool = True
) -> tuple[ProductSummary, list[str]]:
    """Extract one product sheet, as seen by a shopper in ``region``.

    Returns the five standardized fields and the agent's non-fatal warnings (product not
    shippable to the region, fallback to another actor...). ``extract_product_data`` +
    ``summarize`` is used rather than the agent's own ``extract_product`` shortcut,
    although both yield the same five fields: only the full record carries ``warnings``.

    The timeout covers the wait for a semaphore slot as well as the extraction itself, so
    the endpoint's latency stays bounded even when every slot is busy.
    """
    # Dans le `try` : `_apply_region` route l'URL, et le routage refuse d'emblée
    # une plateforme qu'aucun scrapeur ne sait lire. Appelée au-dessus, cette
    # exception-là échappait au traducteur d'erreurs et remontait nue.
    try:
        options = _apply_region(region, url)
    except Exception as exc:
        error = _http_error(exc)
        logger.info(
            "Extraction refused url=%s region=%s -> %s: %s",
            url, region, error.__class__.__name__, exc,
        )
        raise error from exc

    logger.info(
        "Extraction started url=%s region=%s use_agent=%s force_actor=%s",
        url,
        region,
        use_agent,
        options.get("force_actor"),
    )
    def on_agent_event(kind: str, payload: Any) -> None:
        """Forward the agent's token accounting to the service log.

        The agent reports its consumption through this channel whether the run
        succeeded, failed or hit its step limit -- the failed runs being precisely the
        expensive ones. The other kind worth a line is `agent_error`: the agent catches
        it, falls back to the deterministic pipeline and answers 200, so nothing else
        in this module ever sees it. That silence hid a 400 from the Anthropic API on
        EVERY extraction -- each product came back with the template description and a
        warning nobody reads. Every other event kind feeds the CLI and is ignored here.
        """
        if kind == "usage":
            logger.info("Extraction usage url=%s region=%s %s", url, region, payload)
        elif kind == "agent_error":
            logger.warning(
                "Extraction agent degraded url=%s region=%s error=%s: %s",
                url, region, type(payload).__name__, payload,
            )

    try:
        async with asyncio.timeout(products_settings.EXTRACTION_TIMEOUT_SECONDS):
            async with _semaphore:
                product = await _on_browser_capable_loop(
                    lambda: extract_product_data(url, use_agent=use_agent,
                                                 on_event=on_agent_event, **options)
                )
    except Exception as exc:
        error = _http_error(exc)
        logger.warning(
            "Extraction failed url=%s region=%s error=%s -> %s: %s",
            url,
            region,
            type(exc).__name__,
            error.__class__.__name__,
            # Without the message the log only says "something upstream failed": the
            # actor id, the run id and the reason all live in there. ConfigError is the
            # one exception -- its message names a credential and may quote its value.
            "<redacted>" if isinstance(exc, ConfigError) else exc,
            exc_info=not isinstance(exc, ExtractionError | TimeoutError),
        )
        raise error from exc

    warnings = list(product.warnings)
    if warnings:
        logger.warning("Extraction warnings url=%s region=%s: %s", url, region, warnings)
    return summarize(product, source_url=url), warnings


# ---------------------------------------------------------------------------
# 4. Startup check
# ---------------------------------------------------------------------------
def _browser_binary_present(executable: str) -> bool:
    """One ``stat`` on the resolved Chromium path.

    Kept synchronous so the filesystem call does not sit in a coroutine (ruff ASYNC240).
    It runs once at startup and is far too cheap to be worth an async filesystem layer.
    """
    return os.path.exists(executable)


async def _resolve_chromium_path() -> str:
    """Start the Playwright driver just long enough to resolve Chromium's path.

    The **async** API on purpose: the synchronous one refuses to run inside a live event
    loop, which is exactly where the FastAPI lifespan calls this. No browser is launched.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        return playwright.chromium.executable_path


async def check_browser_available() -> bool:
    """Warn (never crash) when Chromium is missing.

    Playwright is required by every Playwright-routed extraction, but the rest of the API
    -- F1 included -- works fine without it, so a missing browser must not stop the boot.

    Goes through ``_on_browser_capable_loop`` like a real extraction, so the probe answers
    the question that actually matters: can *this deployment* start a browser?
    """
    try:
        executable = await _on_browser_capable_loop(_resolve_chromium_path)
    except Exception as exc:  # startup probe: any failure is only a warning
        logger.warning(
            "Playwright/Chromium is unavailable (%s: %s). Extractions routed to a browser "
            "will fail with 502. Fix with: playwright install chromium",
            type(exc).__name__,
            exc,
        )
        return False

    if not _browser_binary_present(executable):
        logger.warning(
            "Chromium is not installed at %s. Extractions routed to a browser will fail "
            "with 502. Fix with: playwright install chromium",
            executable,
        )
        return False
    logger.info("Chromium available at %s", executable)
    return True


# Regional adapter clones must exist before the first request routes to Apify.
register_region_adapters()
