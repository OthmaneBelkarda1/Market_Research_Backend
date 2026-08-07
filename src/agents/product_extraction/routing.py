"""
URL detection & extraction-strategy selection
=============================================

Answers one question: *how* should this URL be scraped?

  Playwright   — render the page ourselves. Free, instant, works on the long
                 tail (Shopify/WooCommerce/BigCommerce stores, brand sites).
  Apify actor  — a hosted scraper with residential proxies and anti-bot
                 handling. Used for marketplaces that block automation
                 (Temu redirects to /login, Amazon serves CAPTCHAs,
                 AliExpress renders through a signed API).

Adding a new site is a ONE-LINE change: register the domain against an actor
key from `actors.ACTOR_ADAPTERS` (add the adapter there if it's a new actor).

    DOMAIN_ROUTES = (
        ...
        DomainRoute(("shein.com",), platform="shein", actor="generic_ecommerce"),
    )

or at runtime, without touching this file:

    routing.register_domain("shein.com", actor="generic_ecommerce")
"""

from dataclasses import dataclass
from urllib.parse import urlparse

from .config import UnsupportedUrlError

# Strategy names are also what lands in ProductData.strategy.
PLAYWRIGHT = "playwright"
APIFY = "apify"


@dataclass(frozen=True)
class DomainRoute:
    """A domain family that must be scraped through a specific Apify actor."""

    domains: tuple[str, ...]
    platform: str
    actor: str
    reason: str = "marketplace blocks direct scraping"


# ---------------------------------------------------------------------------
# 1. The routing table  (domain suffix -> Apify actor adapter key)
# ---------------------------------------------------------------------------
# `actor` values are keys of actors.ACTOR_ADAPTERS.
DOMAIN_ROUTES: tuple[DomainRoute, ...] = (
    DomainRoute(
        domains=("temu.com",),
        platform="temu",
        actor="temu",
        reason="Temu redirects stateless browsers to /login.html",
    ),
    DomainRoute(
        domains=("amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.es",
                 "amazon.it", "amazon.ca", "amazon.com.mx", "amazon.com.br", "amazon.in",
                 "amazon.co.jp", "amazon.com.au", "amazon.nl", "amazon.se", "amazon.pl",
                 "amazon.ae", "amazon.sa", "amazon.eg", "amazon.com.tr", "amazon.sg"),
        platform="amazon",
        actor="amazon",
        reason="Amazon serves CAPTCHAs / 'Robot Check' to headless browsers",
    ),
    DomainRoute(
        domains=("aliexpress.com", "aliexpress.us", "aliexpress.ru", "aliexpress.fr",
                 "aliexpress.es", "aliexpress.it", "aliexpress.pl", "aliexpress.nl"),
        platform="aliexpress",
        actor="aliexpress",
        reason="AliExpress renders product data through a signed internal API",
    ),
    DomainRoute(
        domains=("walmart.com", "walmart.ca"),
        platform="walmart",
        actor="walmart",
        reason="Walmart fingerprints automated browsers (PerimeterX)",
    ),
    DomainRoute(
        domains=("ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr", "ebay.it", "ebay.es",
                 "ebay.ca", "ebay.com.au", "ebay.ie", "ebay.at", "ebay.ch", "ebay.nl"),
        platform="ebay",
        actor="generic_ecommerce",
        reason="eBay item pages are rate-limited for datacenter IPs",
    ),
    DomainRoute(
        domains=("shein.com", "temu.co.uk", "alibaba.com", "1688.com", "taobao.com",
                 "tmall.com", "lazada.com", "shopee.com"),
        platform="marketplace",
        actor="generic_ecommerce",
        reason="marketplace with aggressive anti-bot protection",
    ),
)

# When Playwright is used but fails (block page, timeout, empty result), this
# actor is tried as a safety net — it accepts arbitrary retail product URLs.
DEFAULT_FALLBACK_ACTOR = "generic_ecommerce"

# Hints used only to label the platform of self-hosted stores.
_PLATFORM_HINTS = (
    ("/products/", "shopify"),      # myshopify + custom-domain Shopify stores
    ("/product/", "woocommerce"),
    ("/dp/", "amazon-like"),
    ("/itm/", "ebay-like"),
)


@dataclass(frozen=True)
class Route:
    """The decision, plus enough context to explain it to a human or an LLM."""

    url: str
    domain: str
    platform: str
    strategy: str                    # PLAYWRIGHT | APIFY
    actor: str | None                # adapter key when strategy == APIFY
    fallback_actor: str | None       # tried if the primary strategy fails
    reason: str

    def describe(self) -> str:
        target = f"Apify actor '{self.actor}'" if self.strategy == APIFY else "Playwright"
        return f"{self.domain} -> {target} ({self.reason})"


# ---------------------------------------------------------------------------
# 2. Detection
# ---------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    """Accept 'example.com/p/1' as well as a full URL; reject anything else."""
    url = (url or "").strip().strip("<>\"'")
    if not url:
        raise UnsupportedUrlError("No URL provided.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        raise UnsupportedUrlError(f"Not a valid product URL: {url!r}")
    return url


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domain: str) -> bool:
    """Suffix match on label boundaries: 'amazon.com' matches 'smile.amazon.com'
    but not 'notamazon.com'."""
    return host == domain or host.endswith("." + domain)


def _platform_hint(url: str, host: str) -> str:
    path = urlparse(url).path.lower()
    if "myshopify.com" in host:
        return "shopify"
    for fragment, platform in _PLATFORM_HINTS:
        if fragment in path:
            return platform
    return "generic"


def detect_route(url: str) -> Route:
    """URL -> extraction plan. Pure function, no network access."""
    url = normalize_url(url)
    host = _host(url)

    for route in DOMAIN_ROUTES:
        if any(_matches(host, domain) for domain in route.domains):
            return Route(
                url=url,
                domain=host,
                platform=route.platform,
                strategy=APIFY,
                actor=route.actor,
                # If the dedicated actor fails, the official multi-marketplace
                # one is worth a try — it covers most of these sites too.
                fallback_actor=(None if route.actor == DEFAULT_FALLBACK_ACTOR
                                else DEFAULT_FALLBACK_ACTOR),
                reason=route.reason,
            )

    return Route(
        url=url,
        domain=host,
        platform=_platform_hint(url, host),
        strategy=PLAYWRIGHT,
        actor=None,
        fallback_actor=DEFAULT_FALLBACK_ACTOR,
        reason="no dedicated actor registered; the page can be rendered directly",
    )


# ---------------------------------------------------------------------------
# 3. Runtime extension
# ---------------------------------------------------------------------------
def register_domain(*domains: str, actor: str, platform: str = "custom",
                    reason: str = "custom route") -> None:
    """Route extra domains to an Apify actor without editing this module.
    New routes are prepended so they take precedence over the built-ins."""
    global DOMAIN_ROUTES
    DOMAIN_ROUTES = (
        DomainRoute(tuple(d.lower().removeprefix("www.") for d in domains),
                    platform=platform, actor=actor, reason=reason),
    ) + DOMAIN_ROUTES


def supported_routes() -> list[str]:
    """Human-readable summary of the routing table (used by the CLI/agent)."""
    lines = [f"{r.platform:<12} {', '.join(r.domains[:3])}"
             f"{' …' if len(r.domains) > 3 else ''}  ->  apify:{r.actor}"
             for r in DOMAIN_ROUTES]
    lines.append(f"{'everything else':<12}  ->  playwright "
                 f"(fallback apify:{DEFAULT_FALLBACK_ACTOR})")
    return lines
