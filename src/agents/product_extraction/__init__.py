"""
product_extraction — universal e-commerce product extractor
===========================================================

    from product_extraction import extract_product, to_json

    product = await extract_product("https://www.amazon.com/dp/B0CX23V2ZK")
    print(to_json(product))
    # {"name", "description", "category", "image_url", "source_url"}
    # — same five fields for every website. extract_product_data() returns the
    # full internal record (price, specs, seller, shipping, …) when needed.

Modules (one responsibility each):

    routing.py            URL detection + extraction-strategy selection
    fetching.py           Playwright browser control
    parsing.py            HTML -> fields (JSON-LD, Shopify, microdata, OG, heuristics)
    playwright_source.py  Playwright backend
    actors.py             Apify actor registry (add a site here)
    apify_source.py       Apify backend (REST: run -> poll -> dataset)
    normalization.py      merging, cleaning, flat fields -> ProductData
    schema.py             the canonical output contract
    pipeline.py           deterministic orchestration + fallbacks
    agent.py              the LangChain agent on top
    formatting.py         JSON / terminal output
"""

from .actors import ACTOR_ADAPTERS, ActorAdapter, register_adapter
from .agent import extract_product, extract_product_data
from .config import (
    TARGET_COUNTRY,
    TARGET_LOCALE,
    ActorRunError,
    ConfigError,
    ExtractionError,
    PageLoadError,
    UnsupportedUrlError,
)
from .formatting import json_schema, pretty_print, print_summary, to_dict, to_json
from .normalization import summarize
from .pipeline import extract_deterministic
from .routing import detect_route, register_domain, supported_routes
from .schema import (
    Price,
    ProductData,
    ProductSummary,
    Promotion,
    Rating,
    Seller,
    Shipping,
    Variant,
)

__all__ = [
    "extract_product",
    "extract_product_data",
    "extract_deterministic",
    "ProductSummary",
    "ProductData",
    "summarize",
    "Price",
    "Rating",
    "Variant",
    "Seller",
    "Shipping",
    "Promotion",
    "to_json",
    "to_dict",
    "pretty_print",
    "print_summary",
    "json_schema",
    "detect_route",
    "supported_routes",
    "register_domain",
    "register_adapter",
    "ActorAdapter",
    "ACTOR_ADAPTERS",
    "TARGET_COUNTRY",
    "TARGET_LOCALE",
    "ExtractionError",
    "ConfigError",
    "PageLoadError",
    "ActorRunError",
    "UnsupportedUrlError",
]
