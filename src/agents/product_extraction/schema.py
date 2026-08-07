"""
Canonical product schema
========================

The ONE output contract of this package. Every source — Playwright-rendered
HTML, an Apify actor, or the LLM normalizer — is eventually squeezed into
`ProductData`, so callers get the same JSON shape for Amazon, Temu, a random
Shopify store, or a site nobody has ever routed before.

Design rules:
  * Every field is optional. A missing field means "the page didn't say", never
    a crash — partial data is still useful data.
  * `specifications` / `metadata` are dict[str, Any] (NOT dict[str, str]): the
    LLM routinely emits list values (e.g. Categories: ['A', 'B']) and a
    str-typed dict rejects them with a validation error.
  * Nested objects (price, rating, seller, shipping) instead of 30 flat keys —
    it keeps the JSON readable and lets a whole block be absent.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class _Model(BaseModel):
    """Base class that tolerates explicit nulls.

    LLMs answer `"rating": null` for anything a page doesn't mention, which
    would fail validation on fields that default to an object or a list. Since
    every scalar here already defaults to None, simply dropping null keys gives
    the intended result — and keeps a whole extraction from failing over one
    absent block.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, list):
                value = [item for item in value if item is not None]
            cleaned[key] = value
        return cleaned


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
class Price(_Model):
    """Current price plus whatever the page said about the pre-discount one."""

    amount: float | None = Field(default=None, description="Current price as a number")
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. USD, EUR, MAD")
    original_amount: float | None = Field(
        default=None, description="Pre-discount / list / RRP price if shown"
    )
    discount_percent: float | None = Field(
        default=None, description="Discount off the original price, 0-100"
    )
    price_text: str | None = Field(
        default=None, description="Price exactly as printed on the page, e.g. 'US $24.99'"
    )
    price_range: str | None = Field(
        default=None, description="For variant-priced products, e.g. '$19.99 - $29.99'"
    )


class Rating(_Model):
    value: float | None = Field(default=None, description="Average rating")
    scale: float | None = Field(default=5, description="Maximum of the rating scale")
    review_count: int | None = Field(default=None, description="Number of reviews/ratings")
    rating_text: str | None = Field(default=None, description="As printed, e.g. '4.8 (10K+)'")


class Variant(_Model):
    """One selectable option. Flat on purpose: a variant may be a single axis
    ('Color: Red') or a full combination ('Red / XL') depending on the site."""

    name: str | None = Field(default=None, description="Axis name, e.g. Color, Size, Style")
    value: str | None = Field(default=None, description="Option value, e.g. Red, XL")
    sku: str | None = None
    price: float | None = None
    currency: str | None = None
    availability: str | None = None
    image: str | None = None
    selected: bool | None = Field(default=None, description="Whether it is the default option")
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra per-variant data the site exposes (stock, option codes, …)",
    )


class Seller(_Model):
    name: str | None = None
    url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    is_official_store: bool | None = None
    location: str | None = Field(default=None, description="Seller / ships-from country or city")
    followers: int | None = None


class Shipping(_Model):
    free_shipping: bool | None = None
    cost: float | None = None
    currency: str | None = None
    estimated_delivery: str | None = Field(
        default=None, description="As printed, e.g. 'Delivery by Aug 12' or '7-15 days'"
    )
    ships_from: str | None = None
    ships_to: str | None = None
    methods: list[str] = Field(default_factory=list, description="Named shipping options")
    returns: str | None = Field(default=None, description="Return / refund policy text")


class Promotion(_Model):
    label: str | None = Field(default=None, description="Short badge text, e.g. 'Limited deal'")
    description: str | None = None
    discount_percent: float | None = None
    coupon_code: str | None = None
    ends_at: str | None = None


# ---------------------------------------------------------------------------
# The delivered output
# ---------------------------------------------------------------------------
class ProductSummary(_Model):
    """What callers actually receive: one product, five fields, same shape for
    every website. The full `ProductData` record below is still assembled
    internally — it is what makes the paragraph and the category accurate — but
    only this is returned unless the caller asks for everything (--full)."""

    name: str | None = Field(default=None, description="Product name")
    description: str | None = Field(
        default=None,
        description=("Short paragraph describing the product: what it is, brand, "
                     "price, key characteristics, availability"),
    )
    category: str | None = Field(default=None, description="Product category")
    image_url: str | None = Field(default=None, description="Main product image")
    source_url: str | None = Field(default=None, description="The URL that was given")


# ---------------------------------------------------------------------------
# The internal record
# ---------------------------------------------------------------------------
Strategy = Literal["playwright", "apify"]


class ProductData(_Model):
    """Standardized product record — identical shape for every website."""

    # --- provenance (filled in by the pipeline, never by the LLM) ----------
    url: str | None = Field(default=None, description="Product URL that was requested")
    final_url: str | None = Field(default=None, description="URL after redirects")
    source_domain: str | None = None
    strategy: Strategy | None = Field(
        default=None, description="How the data was obtained: playwright or apify"
    )
    source: str | None = Field(
        default=None, description="Concrete source, e.g. 'playwright' or 'apify:junglee/Amazon-crawler'"
    )
    extracted_at: str | None = Field(
        default=None, description="UTC ISO-8601 timestamp of extraction"
    )
    country: str | None = Field(
        default=None,
        description=("Shopper country the page was viewed from (ISO 3166-1). "
                     "Prices, shipping and availability are relative to it."),
    )

    # --- core product ------------------------------------------------------
    title: str | None = Field(default=None, description="Title exactly as printed")
    name: str | None = Field(
        default=None,
        description="Clean product name extracted from the title (feeds ProductSummary)",
    )
    description: str | None = None
    short_description: str | None = Field(
        default=None,
        description="One-paragraph summary of the product (feeds ProductSummary)",
    )
    brand: str | None = None
    category: str | None = Field(default=None, description="Most specific category")
    categories: list[str] = Field(
        default_factory=list, description="Breadcrumb trail, broad -> specific"
    )
    sku: str | None = Field(default=None, description="Seller's SKU / item id / ASIN")
    identifiers: dict[str, Any] = Field(
        default_factory=dict,
        description="Other ids: gtin, upc, ean, mpn, isbn, asin, model_number",
    )

    price: Price = Field(default_factory=Price)
    availability: str | None = Field(
        default=None, description="Normalized: in_stock | out_of_stock | preorder | limited | unknown"
    )
    availability_text: str | None = Field(default=None, description="As printed on the page")
    stock_quantity: int | None = None

    images: list[str] = Field(default_factory=list, description="Absolute image URLs, main first")
    videos: list[str] = Field(default_factory=list)

    rating: Rating = Field(default_factory=Rating)
    specifications: dict[str, Any] = Field(
        default_factory=dict, description="Spec table / attributes as key -> value"
    )
    features: list[str] = Field(default_factory=list, description="Bullet-point features")
    variants: list[Variant] = Field(default_factory=list)
    seller: Seller = Field(default_factory=Seller)
    shipping: Shipping = Field(default_factory=Shipping)
    promotions: list[Promotion] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Anything else worth keeping: sold count, badges, condition, warranty, ...",
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal problems hit while extracting"
    )


class ProductDraft(_Model):
    """What the LLM is asked to produce.

    Same fields as ProductData minus the provenance block — those are facts the
    pipeline knows and the model must not invent (or overwrite).
    """

    title: str | None = Field(
        default=None, description="The title exactly as the page prints it"
    )
    name: str | None = Field(
        default=None,
        description=(
            "The PRODUCT NAME alone, extracted from the title. Strip the store "
            "or site name, separators and everything after them (| - – — ::), "
            "SEO/marketing tails ('Buy online', 'Free Shipping', 'Official "
            "Store', 'Best Price'), and trailing option/SKU qualifiers "
            "('- Black, 3XL', '(Pack of 2)', 'ref. A2A1K'). Keep the words that "
            "actually name the product, usually 1-8 words. "
            "'BMotivated | Gym Wear - Free shipping' -> 'BMotivated'."
        ),
    )
    description: str | None = None
    short_description: str | None = Field(
        default=None,
        description=(
            "ONE short paragraph (2-4 sentences, max ~70 words) describing this "
            "product for someone who cannot see the page: what it is, brand, "
            "price with currency, the 2-3 characteristics that matter, and stock "
            "status. Plain prose, no bullet points, no marketing slogans, only "
            "facts present in the evidence."
        ),
    )
    brand: str | None = None
    category: str | None = None
    categories: list[str] = Field(default_factory=list)
    sku: str | None = None
    identifiers: dict[str, Any] = Field(default_factory=dict)
    price: Price = Field(default_factory=Price)
    availability: str | None = None
    availability_text: str | None = None
    stock_quantity: int | None = None
    images: list[str] = Field(default_factory=list)
    videos: list[str] = Field(default_factory=list)
    rating: Rating = Field(default_factory=Rating)
    specifications: dict[str, Any] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    seller: Seller = Field(default_factory=Seller)
    shipping: Shipping = Field(default_factory=Shipping)
    promotions: list[Promotion] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
