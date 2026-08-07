"""
Extraction pipeline (no LLM)
============================

Glues routing -> backend -> normalization together and applies the fallback
policy:

    dedicated actor domain ──────────────► Apify actor
    everything else ─────► Playwright ──► (blocked/failed?) ──► fallback actor

This is the deterministic core. `agent.py` puts an LLM on top of it, but the
pipeline alone already returns a valid `ProductData` — which is what makes the
package usable without an OpenAI key, and what the agent falls back to.
"""

from .config import ExtractionError, PageLoadError
from .normalization import build_product
from .playwright_source import extract_with_playwright
from .routing import APIFY, Route, detect_route
from .schema import ProductData
from .sources import SourceResult


async def gather_source(url: str, *, route: Route | None = None,
                        force_actor: str | None = None,
                        allow_fallback: bool = True,
                        **playwright_options) -> SourceResult:
    """Fetch raw product material using the best available backend.

    force_actor      — skip routing and use this actor adapter key.
    allow_fallback   — when Playwright is blocked, retry through the actor
                       registered as the route's fallback.
    """
    # Imported lazily so Playwright-only users never need an Apify token and
    # httpx import stays out of the hot path.
    from .apify_source import extract_with_apify

    route = route or detect_route(url)

    if force_actor:
        return await extract_with_apify(route.url, force_actor)

    if route.strategy == APIFY:
        try:
            return await extract_with_apify(route.url, route.actor)
        except ExtractionError as exc:
            if not (allow_fallback and route.fallback_actor):
                raise
            fallback = await extract_with_apify(route.url, route.fallback_actor)
            fallback.warnings.insert(0, f"actor '{route.actor}' failed ({exc}); "
                                        f"used '{route.fallback_actor}' instead")
            return fallback

    try:
        result = await extract_with_playwright(route.url, **playwright_options)
    except (PageLoadError, ExtractionError) as exc:
        if not (allow_fallback and route.fallback_actor):
            raise
        fallback = await extract_with_apify(route.url, route.fallback_actor)
        fallback.warnings.insert(0, f"Playwright failed ({exc}); used Apify instead")
        return fallback

    # Rendered fine but yielded almost nothing (SPA that never hydrated, soft
    # block): the actor is worth a try before giving up.
    if allow_fallback and route.fallback_actor and not _is_useful(result):
        try:
            fallback = await extract_with_apify(route.url, route.fallback_actor)
            fallback.warnings.insert(
                0, "Playwright returned too little data; used Apify instead"
            )
            return fallback
        except ExtractionError as exc:
            result.warnings.append(f"Apify fallback also failed: {exc}")
    return result


def _is_useful(result: SourceResult) -> bool:
    """A result is worth keeping if it has a title AND something commercial."""
    fields = result.fields
    has_commerce = any(fields.get(key) for key in
                       ("price_amount", "price_text", "images", "availability_text", "sku"))
    return bool(fields.get("title")) and has_commerce


def to_product(result: SourceResult, url: str) -> ProductData:
    """SourceResult -> canonical record (deterministic fields only)."""
    fields = dict(result.fields)
    fields["warnings"] = list(result.warnings) + list(fields.get("warnings") or [])
    return build_product(
        fields,
        url=url,
        final_url=result.final_url,
        strategy=result.strategy,
        source=result.source,
    )


async def extract_deterministic(url: str, **options) -> ProductData:
    """Full extraction without any LLM call."""
    route = detect_route(url)
    result = await gather_source(url, route=route, **options)
    return to_product(result, route.url)
