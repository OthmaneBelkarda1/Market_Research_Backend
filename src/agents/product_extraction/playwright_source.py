"""
Playwright extraction backend
=============================

fetch (fetching.py) -> parse (parsing.py) -> merge (normalization.py)
-> `SourceResult`.

This is the default path for every domain without a dedicated Apify actor:
Shopify/WooCommerce/Magento stores, brand sites, small retailers.
"""

import re

from .config import MAX_PAGE_TEXT_CHARS, PageLoadError
from .fetching import PageFetcher, RenderedPage
from .normalization import merge_partials
from .parsing import STRUCTURED_EXTRACTORS, parse_page
from .sources import SourceResult

# Boilerplate that eats the LLM's context budget without carrying product data.
_NOISE_LINE_RE = re.compile(
    r"^(cookie|we use cookies|accept all|sign in|log in|create account|newsletter|"
    r"subscribe|follow us|©|copyright|privacy policy|terms of)", re.I
)


def clean_page_text(text: str, limit: int = MAX_PAGE_TEXT_CHARS) -> str:
    """Trim the visible text to the part worth reading: drop chrome lines and
    repeated blanks, then cap the length."""
    lines: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or _NOISE_LINE_RE.match(line):
            continue
        key = line.lower()
        if key in seen:            # menus repeat the same labels many times
            continue
        seen.add(key)
        lines.append(line)
    joined = "\n".join(lines)
    return joined[:limit]


def build_result(page: RenderedPage, url: str) -> SourceResult:
    """Parse an already-rendered page into a SourceResult."""
    partials, warnings = parse_page(page)
    fields = merge_partials([partial for _, partial in partials])

    # Which extractor actually supplied each winning value? Values that came
    # from layout heuristics rather than declared markup are marked `soft`, so
    # the LLM is allowed to correct them later.
    winners: dict[str, str] = {}
    for name, partial in partials:
        for key, value in partial.items():
            if value not in (None, "", [], {}):
                winners.setdefault(key, name)
    soft = {key for key, name in winners.items() if name not in STRUCTURED_EXTRACTORS}

    if not fields.get("title"):
        warnings.append("no title found — the page may not be a product page")
    return SourceResult(
        strategy="playwright",
        source="playwright",
        url=url,
        final_url=page.url,
        fields=fields,
        soft_fields=soft,
        context=clean_page_text(page.text),
        warnings=warnings,
    )


async def extract_with_playwright(url: str, **options) -> SourceResult:
    """Render `url` and deterministically extract everything the HTML exposes.

    Raises PageLoadError when the page cannot be loaded OR when what comes back
    is an anti-bot interstitial — the caller then routes to an Apify actor.
    """
    async with PageFetcher(**options) as fetcher:
        page = await fetcher.fetch(url)

    if page.looks_blocked():
        raise PageLoadError(
            f"{url} returned an anti-bot/blocked page (status={page.status}); "
            "an Apify actor is needed for this site"
        )
    return build_result(page, url)
