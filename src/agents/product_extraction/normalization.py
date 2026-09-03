"""
Data normalization
==================

Everything upstream (HTML parsers, Apify actor mappers, the LLM) speaks the
same intermediate language: a FLAT dict of `partial fields`. This module owns

  * the parsing primitives (price strings, numbers, availability wording),
  * `merge_partials` — first non-empty value wins, in source-priority order,
  * `build_product` — flat dict -> nested `ProductData`,
  * `overlay_reliable` — put deterministic values back on top of the LLM draft.

Keeping it here means a new source only has to emit flat keys; it never has to
know how the final JSON is assembled.

Flat keys understood by `build_product` (all optional):
    title description brand category categories sku identifiers
    price_amount price_currency price_original price_discount_percent
    price_text price_range
    availability availability_text stock_quantity images videos
    rating_value rating_scale rating_count rating_text
    specifications features variants
    seller_name seller_url seller_rating seller_review_count
    seller_is_official seller_location seller_followers
    shipping_free shipping_cost shipping_currency shipping_delivery
    ships_from ships_to shipping_methods returns
    promotions metadata warnings
"""

import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from .config import INCLUDE_VARIANTS, TARGET_COUNTRY
from .schema import (
    Price,
    ProductData,
    ProductDraft,
    ProductSummary,
    Promotion,
    Rating,
    Seller,
    Shipping,
    Variant,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# 1. Scalars
# ---------------------------------------------------------------------------
# Symbol -> ISO code. Covers the currencies these marketplaces actually print.
_CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "usd": "USD",
    "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₽": "RUB", "₩": "KRW",
    "₺": "TRY", "R$": "BRL", "A$": "AUD", "C$": "CAD", "CHF": "CHF",
    "zł": "PLN", "kr": "SEK", "₪": "ILS", "₦": "NGN", "₫": "VND", "฿": "THB",
    "د.إ": "AED", "ر.س": "SAR", "DH": "MAD", "Dh": "MAD", "MAD": "MAD",
}
# 3-letter code printed next to the amount, e.g. "MAD 39.99" or "39.99 USD".
_ISO_CODE_RE = re.compile(r"\b([A-Z]{3})\b")
_NUMBER_RE = re.compile(r"\d[\d.,\s ]*\d|\d")


def to_float(value: Any) -> float | None:
    """Best-effort number out of anything ('US $1,234.56' -> 1234.56)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = _NUMBER_RE.search(value.replace(" ", " "))
    if not match:
        return None
    raw = match.group().strip().replace(" ", "")

    # Decide what the separators mean. European pages write 1.234,56 while the
    # US writes 1,234.56 — the LAST separator is the decimal one when it is
    # followed by exactly 1-2 digits, otherwise both are thousands separators.
    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    sep_pos = max(last_dot, last_comma)
    if sep_pos != -1 and len(raw) - sep_pos - 1 in (1, 2):
        decimal_sep = raw[sep_pos]
        thousands_sep = "," if decimal_sep == "." else "."
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        raw = raw.replace(",", "").replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    """Number of reviews/sold: handles '1,234', '10K+ sold', '2.5k'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    multiplier = 1
    if re.search(r"\d\s*[km]\b|\d[km]\+", text):
        multiplier = 1000 if "k" in text else 1_000_000
        text = re.sub(r"[km]", "", text, count=1)
    number = to_float(text)
    return int(number * multiplier) if number is not None else None


def parse_currency(text: str | None) -> str | None:
    """Pull an ISO currency code out of a price string."""
    if not text:
        return None
    iso = _ISO_CODE_RE.search(text.upper())
    if iso and iso.group(1) in {
        "USD", "EUR", "GBP", "JPY", "CNY", "INR", "AUD", "CAD", "MAD", "AED",
        "SAR", "BRL", "MXN", "PLN", "SEK", "NOK", "DKK", "CHF", "TRY", "RUB",
        "ZAR", "NGN", "KRW", "SGD", "HKD", "NZD", "ILS", "THB", "VND", "EGP",
    }:
        return iso.group(1)
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return None


def normalize_currency(value: Any) -> str | None:
    """'$' -> 'USD', 'usd' -> 'USD'. Actors report either form (junglee's
    Amazon scraper returns the symbol), the schema promises the ISO code."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if len(value) == 3 and value.isalpha():
        return value.upper()
    return _CURRENCY_SYMBOLS.get(value) or parse_currency(value)


def parse_price(text: Any) -> tuple[float | None, str | None]:
    """'US $24.99' -> (24.99, 'USD'). Returns (None, None) when unparseable."""
    if text is None:
        return None, None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text), None
    if not isinstance(text, str):
        return None, None
    return to_float(text), parse_currency(text)


_IN_STOCK_WORDS = (
    "in stock", "instock", "available", "en stock", "disponible", "add to cart",
    "buy now", "ships", "en existencia", "verfügbar",
)
_OUT_WORDS = (
    "out of stock", "outofstock", "sold out", "unavailable", "soldout",
    "no longer available", "rupture", "agotado", "discontinued",
    # Geo-blocked purchases: the shopper cannot buy it either.
    "cannot be shipped", "can't be shipped", "not available in your",
    "doesn't ship to", "does not ship to",
)


def normalize_availability(text: Any) -> str | None:
    """Free-text or schema.org availability -> one of the canonical states."""
    if isinstance(text, bool):
        return "in_stock" if text else "out_of_stock"
    if not isinstance(text, str) or not text.strip():
        return None
    # Actors shout enum values ('IN_STOCK', 'OutOfStock'); pages write prose.
    # Split both shapes into words before matching.
    spaced = re.sub(r"[_\-]+", " ", text)
    low = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", spaced).lower()
    if any(word in low for word in _OUT_WORDS):
        return "out_of_stock"
    if re.search(r"pre[ -]?order|pre[ -]?sale", low):
        return "preorder"
    if re.search(r"back[ -]?order", low):
        return "backorder"
    if "limited" in low or "low stock" in low or "only" in low and "left" in low:
        return "limited"
    if any(word in low for word in _IN_STOCK_WORDS):
        return "in_stock"
    return "unknown"


# ---------------------------------------------------------------------------
# 2. URLs & images
# ---------------------------------------------------------------------------
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|avif|gif|bmp)(\?|$)", re.I)
_IMAGE_HOST_HINTS = ("img.", "image", "cdn", "media", "kwcdn", "aimg", "ae01", "static")
# Site furniture that og:image/actor payloads sometimes point at.
_NON_PRODUCT_IMG_RE = re.compile(r"logo|favicon|sprite|placeholder|spinner|loader|pixel", re.I)


def clean_images(values: Iterable[Any], base_url: str | None = None) -> list[str]:
    """Absolutize, de-duplicate and drop non-images (some actors put the product
    PAGE url in the images array), preserving order — the first is the main one."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        url = value
        if isinstance(value, dict):  # schema.org ImageObject / actor objects
            url = value.get("url") or value.get("src") or value.get("contentUrl")
        if not isinstance(url, str):
            continue
        url = url.strip()
        if not url or url.startswith("data:"):
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif base_url and not url.startswith("http"):
            url = urljoin(base_url, url)
        if not url.startswith("http"):
            continue
        host = urlparse(url).netloc.lower()
        if not _IMAGE_EXT_RE.search(url) and not any(h in host for h in _IMAGE_HOST_HINTS):
            continue  # looks like a page link, not an image
        if _NON_PRODUCT_IMG_RE.search(url):
            continue
        # Same file served as http:// and https:// is the same image.
        key = url.split("?")[0].split("://", 1)[-1]
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def clean_text(value: Any, limit: int | None = None) -> str | None:
    """Collapse whitespace; return None for empty/placeholder values."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    text = re.sub(r"[ \t ]+", " ", value).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text or text.lower() in {"n/a", "none", "null", "unknown", "-", "—"}:
        return None
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


# ---------------------------------------------------------------------------
# 3. Merging
# ---------------------------------------------------------------------------
def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def merge_partials(partials: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Merge flat dicts in priority order: the FIRST source that provides a
    non-empty value for a key wins. dicts and lists are unioned instead of
    replaced, so a low-priority source can still add specs/images the winner
    didn't have."""
    merged: dict[str, Any] = {}
    for partial in partials:
        for key, value in (partial or {}).items():
            if _is_empty(value):
                continue
            current = merged.get(key)
            if _is_empty(current):
                merged[key] = value
            elif isinstance(current, dict) and isinstance(value, dict):
                merged[key] = {**value, **current}          # keep winner's keys
            elif isinstance(current, list) and isinstance(value, list):
                extra = [v for v in value if v not in current]
                merged[key] = current + extra
    return merged


# ---------------------------------------------------------------------------
# 4. Flat dict -> ProductData
# ---------------------------------------------------------------------------
def _variants(raw: Any) -> list[Variant]:
    out: list[Variant] = []
    for item in raw or []:
        if isinstance(item, Variant):
            out.append(item)
        elif isinstance(item, dict):
            amount, currency = parse_price(item.get("price"))
            out.append(
                Variant(
                    name=clean_text(item.get("name") or item.get("type") or item.get("axis")),
                    value=clean_text(item.get("value") or item.get("title") or item.get("option")),
                    sku=clean_text(item.get("sku") or item.get("id")),
                    price=amount,
                    currency=normalize_currency(item.get("currency")) or currency,
                    availability=normalize_availability(item.get("availability")),
                    image=(clean_images([item.get("image")]) or [None])[0],
                    selected=item.get("selected"),
                    attributes={k: v for k, v in (item.get("attributes") or {}).items()
                                if not _is_empty(v)},
                )
            )
        elif isinstance(item, str) and item.strip():
            out.append(Variant(value=clean_text(item)))
    return out


def _promotions(raw: Any) -> list[Promotion]:
    out: list[Promotion] = []
    for item in raw or []:
        if isinstance(item, Promotion):
            out.append(item)
        elif isinstance(item, dict):
            out.append(
                Promotion(
                    label=clean_text(item.get("label") or item.get("title") or item.get("badge")),
                    description=clean_text(item.get("description") or item.get("text")),
                    discount_percent=to_float(item.get("discount_percent") or item.get("discount")),
                    coupon_code=clean_text(item.get("coupon_code") or item.get("code")),
                    ends_at=clean_text(item.get("ends_at") or item.get("expires")),
                )
            )
        elif isinstance(item, str) and item.strip():
            out.append(Promotion(label=clean_text(item)))
    return out


def _specs(raw: Any) -> dict[str, Any]:
    """Accepts {k: v} or [{'key': k, 'value': v}] / [{'name':..,'value':..}]."""
    if isinstance(raw, dict):
        return {
            str(k).strip(): v
            for k, v in raw.items()
            if str(k).strip() and not _is_empty(v)
        }
    specs: dict[str, Any] = {}
    for item in raw or []:
        if isinstance(item, dict):
            key = item.get("key") or item.get("name") or item.get("label")
            value = item.get("value") if "value" in item else item.get("text")
            if key and not _is_empty(value):
                specs[str(key).strip()] = value
    return specs


def build_product(fields: dict[str, Any], *, url: str, final_url: str | None = None,
                  strategy: str | None = None, source: str | None = None,
                  country: str | None = None,
                  include_variants: bool | None = None) -> ProductData:
    """Assemble the canonical record from merged flat fields.

    include_variants=False (the default, see config.INCLUDE_VARIANTS) returns
    the product at the URL only, with an empty `variants` list.
    """
    if include_variants is None:
        include_variants = INCLUDE_VARIANTS
    base = final_url or url

    price_amount = to_float(fields.get("price_amount"))
    price_text = clean_text(fields.get("price_text"))
    currency = normalize_currency(fields.get("price_currency")) or parse_currency(price_text)
    if price_amount is None and price_text:
        price_amount = to_float(price_text)
    elif price_text and to_float(price_text) != price_amount:
        # The printed string came from a different element than the structured
        # amount (a mini-cart total, an upsell). Trust the number, drop the text.
        price_text = None
    original = to_float(fields.get("price_original"))
    discount = to_float(fields.get("price_discount_percent"))
    if discount is None and original and price_amount and original > price_amount:
        discount = round((original - price_amount) / original * 100, 1)

    availability_text = clean_text(fields.get("availability_text"))
    warnings = list(fields.get("warnings") or [])

    # A missing price is usually not a parsing failure: marketplaces simply
    # hide the price for items they will not ship to the shopper's country.
    # Say so, otherwise a null price reads as a bug.
    if price_amount is None and availability_text and re.search(
        r"cannot be shipped|does(n't| not) ship|not available in your|unavailable in your",
        availability_text, re.I,
    ):
        warnings.append(
            f"no price shown: the seller does not ship this item to "
            f"{country or TARGET_COUNTRY} — re-run with a different country "
            f"(e.g. --country US) to see the price offered elsewhere"
        )
    availability = fields.get("availability")
    if availability not in {"in_stock", "out_of_stock", "preorder", "backorder", "limited", "unknown"}:
        availability = normalize_availability(availability) or normalize_availability(availability_text)

    return ProductData(
        url=url,
        final_url=final_url,
        source_domain=domain_of(base),
        strategy=strategy,
        source=source,
        extracted_at=utc_now_iso(),
        country=country or TARGET_COUNTRY,
        title=clean_text(fields.get("title")),
        name=clean_text(fields.get("name"), limit=120),
        description=clean_text(fields.get("description"), limit=5000),
        short_description=clean_text(fields.get("short_description"), limit=900),
        brand=clean_text(fields.get("brand")),
        category=clean_text(fields.get("category")),
        categories=[c for c in (clean_text(x) for x in fields.get("categories") or []) if c],
        sku=clean_text(fields.get("sku")),
        identifiers={k: v for k, v in (fields.get("identifiers") or {}).items() if not _is_empty(v)},
        price=Price(
            amount=price_amount,
            currency=(currency or "").upper() or None,
            original_amount=original,
            discount_percent=discount,
            price_text=price_text,
            price_range=clean_text(fields.get("price_range")),
        ),
        availability=availability,
        availability_text=availability_text,
        stock_quantity=to_int(fields.get("stock_quantity")),
        images=clean_images(fields.get("images") or [], base),
        videos=[v for v in fields.get("videos") or [] if isinstance(v, str)],
        rating=Rating(
            value=to_float(fields.get("rating_value")),
            scale=to_float(fields.get("rating_scale")) or 5,
            review_count=to_int(fields.get("rating_count")),
            rating_text=clean_text(fields.get("rating_text")),
        ),
        specifications=_specs(fields.get("specifications")),
        features=[f for f in (clean_text(x) for x in fields.get("features") or []) if f],
        variants=_variants(fields.get("variants")) if include_variants else [],
        seller=Seller(
            name=clean_text(fields.get("seller_name")),
            url=clean_text(fields.get("seller_url")),
            rating=to_float(fields.get("seller_rating")),
            review_count=to_int(fields.get("seller_review_count")),
            is_official_store=fields.get("seller_is_official"),
            location=clean_text(fields.get("seller_location")),
            followers=to_int(fields.get("seller_followers")),
        ),
        shipping=Shipping(
            free_shipping=fields.get("shipping_free"),
            cost=to_float(fields.get("shipping_cost")),
            currency=normalize_currency(fields.get("shipping_currency")),
            estimated_delivery=clean_text(fields.get("shipping_delivery")),
            ships_from=clean_text(fields.get("ships_from")),
            ships_to=clean_text(fields.get("ships_to")),
            methods=[m for m in (clean_text(x) for x in fields.get("shipping_methods") or []) if m],
            returns=clean_text(fields.get("returns")),
        ),
        promotions=_promotions(fields.get("promotions")),
        metadata={k: v for k, v in (fields.get("metadata") or {}).items() if not _is_empty(v)},
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 5. The delivered summary
# ---------------------------------------------------------------------------
# Page titles are built for search engines, not for humans:
#   "BMotivated | Gym Wear - Free shipping"
#   "Arrival T-Shirt - Black | Gymshark"
#   "Thermal Shirt, Slim Fit (Pack of 2) - ref. A2A1K"
# `name` must hold the product, not the shop's SEO string.
_TITLE_SPLIT_RE = re.compile(r"\s+[|»·:+]{1,2}\s+|\s+[-–—]\s+")

# Call to action glued in front of the name: "Get BMotivated", "اطلب الآن …".
_CTA_VERB_RE = re.compile(
    r"^(?:get|buy|order|shop|grab|"
    r"commandez|achetez|obtenez|profitez|"
    r"اطلب|اشتري|اشتر|احصل\s+على)\b[\s:،.\-–—]*",
    re.I,
)
# Only stripped when they FOLLOW a call-to-action verb — otherwise "New Balance"
# and "Now Foods" would lose the word that names them.
_CTA_ADVERB_RE = re.compile(
    r"^(?:now|online|today|maintenant|aujourd'hui|الآن|هنا)\b[\s:،.\-–—]*", re.I
)
# Selling points shops staple onto the title, in the languages this store uses.
_NOISE_RE = re.compile(
    r"free\s+(shipping|delivery)|cash\s+on\s+delivery|best\s+price|fast\s+delivery|"
    r"official\s+(store|site|shop)|online\s+store|new\s+arrival|home\s+page|"
    # Anchored on word boundaries: unanchored, "sale" ate the middle of the
    # French "nasale" ("respiration nasale" -> "respiration na") and "promo"
    # would eat "promotion". Every other alternative here is multi-word, so
    # only these three can match inside a longer word.
    r"\b(?:sale|promos?|discounts?)\b|\d+\s?%\s?off|"
    r"livraison\s+(gratuite|rapide)|paiement\s+à\s+la\s+livraison|meilleur\s+prix|"
    r"boutique\s+officielle|"
    r"التوصيل\s+بالمجان|توصيل\s+مجاني|شحن\s+مجاني|الدفع\s+(عند|بعد)\s+الاستلام|"
    r"أفضل\s+سعر|المتجر\s+الرسمي",
    re.I,
)
_TRAILING_QUALIFIER_RE = re.compile(
    r"\s*[\(\[][^)\]]*(pack|lot|set|pcs|pieces|units|ref\.?|sku|model|color|colour|size|"
    r"taille|couleur)[^)\]]*[\)\]]\s*$", re.I
)


def _strip_noise(text: str) -> str:
    """Remove the shop's selling points and any leading call to action, leaving
    the words that name the product."""
    text = _NOISE_RE.sub(" ", text)
    for _ in range(3):                       # "اطلب الآن Get BMotivated"
        current = text.strip()
        after_verb = _CTA_VERB_RE.sub("", current)
        if after_verb == current:
            break
        text = after_verb
        for _ in range(2):                   # the adverbs trailing that verb
            current = text.strip()
            after_adverb = _CTA_ADVERB_RE.sub("", current)
            if after_adverb == current:
                break
            text = after_adverb
    return re.sub(r"\s{2,}", " ", text).strip(" ,،.:+|-–—")


def short_name(title: str | None, *, brand: str | None = None,
               site_name: str | None = None, domain: str | None = None) -> str | None:
    """Extract the product name from a page title, deterministically.

    Splits on the separators shops use, drops the segments that name the SHOP
    (site name, domain, pure marketing) and keeps the first that names the
    PRODUCT. The LLM does this better and wins when it answers; this is the
    --no-llm path and the safety net.
    """
    title = clean_text(title)
    if not title:
        return clean_text(brand)

    shop_words = {w.lower() for w in (site_name, domain) if w}
    if domain:                       # "gymshark.com" -> "gymshark"
        shop_words.add(domain.split(".")[0].lower())

    segments = [s.strip() for s in _TITLE_SPLIT_RE.split(title) if s.strip()]
    kept = []
    for index, segment in enumerate(segments):
        cleaned = _strip_noise(segment)
        if not cleaned:                      # the segment was ONLY selling points
            continue
        # Shops APPEND their name ("Arrival T-Shirt | Gymshark"), so only later
        # segments may be dropped for matching it. The first is kept even when
        # it equals the shop — for many brands the store name IS the product
        # name ("BMotivated | Gym Wear").
        low = cleaned.lower()
        if index and (low in shop_words
                      or (len(cleaned.split()) <= 2
                          and any(word in low for word in shop_words))):
            continue
        kept.append(cleaned)
    if not kept:
        # Every segment was shop name or selling points: take the first one
        # that still says something, else the raw title.
        cleaned_all = (_strip_noise(segment) for segment in segments)
        kept = [next((c for c in cleaned_all if c), segments[0])]
    title = kept[0]

    title = _TRAILING_QUALIFIER_RE.sub("", title).strip(" ,-–—|")
    # A brand-only leftover is still a valid name (that is the "BMotivated" case).
    return clean_text(title, limit=120) or clean_text(brand)



def _fallback_paragraph(product: ProductData) -> str | None:
    """Write the paragraph from the extracted facts when no LLM wrote one
    (--no-llm, or the model failed). Same information, plainer sentences."""
    name = product.name or short_name(product.title, brand=product.brand,
                                      site_name=product.metadata.get("site_name"),
                                      domain=product.source_domain)
    if not name:
        return None

    opening = name
    if product.brand and product.brand.lower() not in name.lower():
        opening = f"{name} by {product.brand}"
    if product.category:
        opening += f", listed under {product.category}"
    sentences = [f"{opening}."]

    price = product.price
    if price.amount is not None:
        money = f"It is priced at {price.amount:g} {price.currency or ''}".rstrip()
        if price.original_amount and price.discount_percent:
            money += (f", down from {price.original_amount:g} "
                      f"({price.discount_percent:g}% off)")
        sentences.append(money + ".")
    elif price.price_range:
        sentences.append(f"Prices range from {price.price_range}.")

    details = []
    for key, value in list(product.specifications.items())[:3]:
        details.append(f"{key}: {value}")
    if not details:
        details = [feature for feature in product.features[:2]]
    if details:
        sentences.append("Key details — " + "; ".join(str(d) for d in details) + ".")

    closing = []
    if product.rating.value is not None:
        closing.append(f"rated {product.rating.value:g}/{product.rating.scale:g}"
                       + (f" from {product.rating.review_count} reviews"
                          if product.rating.review_count else ""))
    if product.availability and product.availability != "unknown":
        closing.append(product.availability.replace("_", " "))
    if product.seller.name:
        closing.append(f"sold by {product.seller.name}")
    if closing:
        sentences.append("It is " + ", ".join(closing) + ".")

    return clean_text(" ".join(sentences), limit=900)


def summarize(product: ProductData, source_url: str | None = None) -> ProductSummary:
    """Full record -> the five fields callers asked for.

    `source_url` is the URL exactly as it was given; `final_url` (after
    redirects) stays in the full record only.
    """
    category = product.category or (product.categories[-1] if product.categories else None)
    name = product.name or short_name(
        product.title,
        brand=product.brand,
        site_name=product.metadata.get("site_name"),
        domain=product.source_domain,
    )
    return ProductSummary(
        name=name,
        description=product.short_description or _fallback_paragraph(product),
        category=category,
        image_url=product.images[0] if product.images else None,
        source_url=source_url or product.url,
    )


# ---------------------------------------------------------------------------
# 6. LLM draft + deterministic facts
# ---------------------------------------------------------------------------
def draft_to_fields(draft: ProductDraft) -> dict[str, Any]:
    """Flatten the model's structured answer back into the flat language."""
    return {
        "title": draft.title,
        "name": draft.name,
        "description": draft.description,
        "short_description": draft.short_description,
        "brand": draft.brand,
        "category": draft.category,
        "categories": draft.categories,
        "sku": draft.sku,
        "identifiers": draft.identifiers,
        "price_amount": draft.price.amount,
        "price_currency": draft.price.currency,
        "price_original": draft.price.original_amount,
        "price_discount_percent": draft.price.discount_percent,
        "price_text": draft.price.price_text,
        "price_range": draft.price.price_range,
        "availability": draft.availability,
        "availability_text": draft.availability_text,
        "stock_quantity": draft.stock_quantity,
        "images": draft.images,
        "videos": draft.videos,
        "rating_value": draft.rating.value,
        "rating_scale": draft.rating.scale,
        "rating_count": draft.rating.review_count,
        "rating_text": draft.rating.rating_text,
        "specifications": draft.specifications,
        "features": draft.features,
        "variants": draft.variants,
        "seller_name": draft.seller.name,
        "seller_url": draft.seller.url,
        "seller_rating": draft.seller.rating,
        "seller_review_count": draft.seller.review_count,
        "seller_is_official": draft.seller.is_official_store,
        "seller_location": draft.seller.location,
        "seller_followers": draft.seller.followers,
        "shipping_free": draft.shipping.free_shipping,
        "shipping_cost": draft.shipping.cost,
        "shipping_currency": draft.shipping.currency,
        "shipping_delivery": draft.shipping.estimated_delivery,
        "ships_from": draft.shipping.ships_from,
        "ships_to": draft.shipping.ships_to,
        "shipping_methods": draft.shipping.methods,
        "returns": draft.shipping.returns,
        "promotions": draft.promotions,
        "metadata": draft.metadata,
    }


# Fields the deterministic extractors are more trustworthy on than the LLM:
# exact numbers and URLs, which a language model can silently mangle.
RELIABLE_FIELDS = (
    "price_amount", "price_currency", "price_original", "price_text",
    "images", "sku", "identifiers", "rating_value", "rating_count",
    "specifications", "availability", "availability_text", "brand", "title",
)


def overlay_reliable(draft_fields: dict[str, Any], reliable: dict[str, Any],
                     soft_fields: set[str] | None = None) -> dict[str, Any]:
    """Deterministic values win for RELIABLE_FIELDS; the draft keeps the rest
    (variants, seller, shipping, promotions, prose) plus anything the parsers
    missed. merge_partials handles the union semantics.

    `soft_fields` are deterministic values that came from layout heuristics —
    they lose to the draft, because a model reading the page beats a regex that
    grabbed the nearest number.
    """
    soft = soft_fields or set()
    trusted = {k: v for k, v in reliable.items()
               if k in RELIABLE_FIELDS and k not in soft and not _is_empty(v)}
    return merge_partials([trusted, draft_fields, reliable])
