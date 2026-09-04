"""
Apify actor adapters
====================

One `ActorAdapter` per hosted scraper: what to send it, and how to translate a
dataset record into the flat field language of `normalization`.

To support a new website:
    1. add an adapter here (actor id + input builder + optional mapper),
    2. point the domain at its key in `routing.DOMAIN_ROUTES`.

The mapper is deliberately allowed to be incomplete: whatever it doesn't map
still reaches the LLM as raw JSON, which fills the gaps. `map_generic` alone
already handles most actors because e-commerce scrapers converge on the same
field names.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import INCLUDE_VARIANTS, TARGET_COUNTRY
from .normalization import parse_price, to_float, to_int

# ---------------------------------------------------------------------------
# 1. Generic mapping helpers
# ---------------------------------------------------------------------------
def pick(record: dict, *keys: str) -> Any:
    """First non-empty value among `keys`, case-insensitively."""
    lowered = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", [], {}):
            return value
    return None


def unwrap_number(value: Any) -> float | None:
    """Actors express money as 3.99, '3.99', '$3.99' or {'value': 3.99}."""
    if isinstance(value, dict):
        value = pick(value, "value", "amount", "price", "current", "raw")
    return to_float(value)


def unwrap_currency(value: Any) -> str | None:
    if isinstance(value, dict):
        value = pick(value, "currency", "currencyCode", "priceCurrency")
    return value if isinstance(value, str) and 2 <= len(value) <= 5 else None


def _text_of(value: Any, *keys: str) -> str | None:
    """A name that may be a plain string or an object ({'name': ...})."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        found = pick(value, *(keys or ("name", "title", "text")))
        return found if isinstance(found, str) else None
    if isinstance(value, list) and value:
        return _text_of(value[0], *keys)
    return None


def _as_list(value: Any) -> list:
    if value in (None, "", {}):
        return []
    return value if isinstance(value, list) else [value]


def _categories(value: Any) -> list[str]:
    """Breadcrumbs arrive as a list, a list of {name}, or 'A › B › C'."""
    if isinstance(value, str):
        return [c.strip() for c in re.split(r"[>›»/|]", value) if c.strip()]
    out = []
    for item in _as_list(value):
        name = item if isinstance(item, str) else _text_of(item)
        if name:
            out.append(name.strip())
    return out


def map_generic(record: dict) -> dict[str, Any]:
    """Field mapping that works across most e-commerce actors.

    Every dedicated mapper below starts from this and overrides the few keys
    that are actor-specific.
    """
    price_raw = pick(record, "price", "currentPrice", "priceLocal", "salePrice",
                     "finalPrice", "priceValue", "priceCurrent", "offerPrice")
    price_text = pick(record, "priceText", "priceString", "priceFormatted", "displayPrice")
    amount = unwrap_number(price_raw)
    currency = (unwrap_currency(price_raw)
                or pick(record, "currency", "priceCurrency", "currencyCode"))
    if amount is None and isinstance(price_text, str):
        amount, parsed_currency = parse_price(price_text)
        currency = currency or parsed_currency

    original = unwrap_number(pick(record, "originalPrice", "listPrice", "oldPrice",
                                  "msrp", "regularPrice", "priceOriginal", "wasPrice",
                                  "retailPrice", "priceBeforeDiscount"))
    rating_raw = pick(record, "rating", "stars", "ratingValue", "averageRating",
                      "reviewRating", "ratingScore")
    seller_raw = pick(record, "seller", "sellerName", "shopName", "storeName",
                      "merchant", "shop", "store", "vendor")
    shipping_raw = pick(record, "shipping", "shippingText", "deliveryText",
                        "delivery", "shippingInfo", "deliveryInfo")

    fields: dict[str, Any] = {
        "title": _text_of(pick(record, "title", "name", "productName", "product_title")),
        "description": _text_of(pick(record, "description", "productDescription",
                                     "desc", "about", "fullDescription")),
        "brand": _text_of(pick(record, "brand", "brandName", "manufacturer", "vendor")),
        "sku": _text_of(pick(record, "sku", "itemId", "productId", "asin", "id",
                             "product_id", "itemNumber")),
        "price_amount": amount,
        "price_currency": currency,
        "price_original": original,
        "price_discount_percent": to_float(pick(record, "discount", "discountPercent",
                                                "discountPercentage", "savingsPercent")),
        "price_text": price_text if isinstance(price_text, str) else None,
        "availability_text": _text_of(pick(record, "availability", "stockStatus",
                                           "inStockText", "availabilityText", "stock")),
        "stock_quantity": to_int(pick(record, "stockQuantity", "quantityAvailable",
                                      "inventory", "stockCount")),
        "rating_value": unwrap_number(rating_raw),
        "rating_count": to_int(pick(record, "reviewsCount", "reviewCount", "ratingsCount",
                                    "numberOfReviews", "reviewsCountInt", "totalReviews",
                                    "ratingCount")),
        "images": [],
        "categories": _categories(pick(record, "breadCrumbs", "breadcrumbs", "categories",
                                       "categoryPath")),
        "category": _text_of(pick(record, "category", "categoryName", "productType")),
        "features": [f for f in _as_list(pick(record, "features", "bulletPoints",
                                              "highlights")) if isinstance(f, str)],
        "seller_name": _text_of(seller_raw),
        "seller_url": (seller_raw.get("url") if isinstance(seller_raw, dict) else None),
        "shipping_delivery": _text_of(shipping_raw) if not isinstance(shipping_raw, (int, float)) else None,
        "shipping_free": pick(record, "freeShipping", "isFreeShipping"),
        "warnings": [],
    }

    # availability booleans ("inStock": true) beat missing text
    in_stock = pick(record, "inStock", "isAvailable", "available")
    if isinstance(in_stock, bool):
        fields["availability"] = "in_stock" if in_stock else "out_of_stock"

    # images: many possible keys, single value or list, string or {url}
    images: list[Any] = []
    for key in ("images", "imageUrls", "galleryImages", "highResolutionImages",
                "pictures", "photos", "imageUrlList", "image", "mainImage",
                "imageUrl", "thumbnailImage", "galleryThumbnails"):
        images.extend(_as_list(record.get(key)))
    fields["images"] = images

    # specifications: {k: v} maps, [{key, value}] lists, or attribute objects
    specs: dict[str, Any] = {}
    for key in ("attributes", "specifications", "specs", "productDetails", "details",
                "productAttributes", "productSpecifications", "information"):
        value = record.get(key)
        if isinstance(value, dict):
            specs.update({str(k): v for k, v in value.items()})
        else:
            for item in _as_list(value):
                if isinstance(item, dict):
                    name = pick(item, "key", "name", "label", "title")
                    val = item.get("value", item.get("text"))
                    if name and val not in (None, "", [], {}):
                        specs.setdefault(str(name), val)
    if specs:
        fields["specifications"] = specs

    # variants: [{...}] or ['Red', 'Blue'] or 'Color: Red, Blue'
    variants: list[Any] = []
    for key in ("variants", "variantAttributes", "options", "productVariants",
                "variantsText", "variations"):
        value = record.get(key)
        if isinstance(value, str):
            variants.extend(part.strip() for part in value.split(",") if part.strip())
        else:
            for item in _as_list(value):
                if isinstance(item, dict):
                    variants.append({
                        "name": pick(item, "key", "name", "type", "attribute", "label"),
                        "value": pick(item, "value", "title", "option", "text", "values"),
                        "sku": pick(item, "sku", "id", "variantId"),
                        "price": unwrap_number(pick(item, "price", "priceValue")),
                        "availability": pick(item, "availability", "inStock"),
                        "image": pick(item, "image", "imageUrl", "thumbnail"),
                    })
                elif isinstance(item, str):
                    variants.append(item)
    if variants:
        fields["variants"] = variants

    # anything notable that has no canonical home
    metadata = {
        key: record[key]
        for key in ("soldCount", "sold", "ordersCount", "condition", "warranty",
                    "isTrending", "demandTier", "estimatedRetailUsd", "bestsellerRanks",
                    "countryOfOrigin", "returnPolicy", "badge", "badges", "position")
        if record.get(key) not in (None, "", [], {})
    }
    if metadata:
        fields["metadata"] = metadata

    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


# ---------------------------------------------------------------------------
# 2. Adapter definition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActorAdapter:
    """Everything the Apify backend needs to use one actor for one URL."""

    key: str
    actor_id: str                                   # 'username/actor-name'
    label: str
    build_input: Callable[[str], dict[str, Any]]
    map_record: Callable[[dict], dict[str, Any]] = map_generic
    # Some actors return several rows (e.g. one per variant); this picks ours.
    select_record: Callable[[list[dict], str], dict | None] | None = None
    # …and some SPLIT one product across rows. When set, this takes the whole
    # dataset and returns the flat fields itself (map_record is then unused).
    aggregate: Callable[[list[dict], str], dict[str, Any]] | None = None
    notes: str = ""
    extra_input: dict[str, Any] = field(default_factory=dict)


def _best_match(items: list[dict], url: str) -> dict | None:
    """Default record selection: prefer the row whose url matches the request."""
    if not items:
        return None
    tail = url.rstrip("/").split("/")[-1].split("?")[0].lower()
    for item in items:
        candidate = str(pick(item, "url", "productUrl", "link", "itemUrl") or "").lower()
        if tail and tail in candidate:
            return item
    return items[0]


# ---------------------------------------------------------------------------
# 3. Per-site mappers
# ---------------------------------------------------------------------------
def map_amazon(record: dict) -> dict[str, Any]:
    """junglee/Amazon-crawler — price/listPrice are {value, currency} objects."""
    fields = map_generic(record)
    price = record.get("price") if isinstance(record.get("price"), dict) else {}
    list_price = record.get("listPrice") if isinstance(record.get("listPrice"), dict) else {}
    fields.update({k: v for k, v in {
        "price_amount": to_float(price.get("value")),
        "price_currency": price.get("currency"),
        "price_original": to_float(list_price.get("value")),
        "rating_value": to_float(record.get("stars")),
        "rating_count": to_int(record.get("reviewsCount")),
        "availability_text": record.get("inStockText"),
        "sku": record.get("asin"),
        "description": record.get("description") or record.get("productDescription"),
    }.items() if v not in (None, "", [], {})})

    identifiers = {k: record[k] for k in ("asin", "gtin", "upc", "ean", "mpn", "model")
                   if record.get(k)}
    if identifiers:
        fields["identifiers"] = identifiers

    # Amazon splits its spec table across three lists of {key, value}.
    specs = dict(fields.get("specifications") or {})
    for key in ("attributes", "productOverview", "manufacturerAttributes"):
        for item in _as_list(record.get(key)):
            if isinstance(item, dict) and item.get("key"):
                specs.setdefault(str(item["key"]), item.get("value"))
    if specs:
        fields["specifications"] = specs

    seller = record.get("seller")
    if isinstance(seller, dict):
        fields["seller_name"] = seller.get("name") or seller.get("businessName")
        fields["seller_url"] = seller.get("url")
        address = seller.get("address")
        if isinstance(address, list) and address:
            fields["seller_location"] = ", ".join(str(part) for part in address)
    elif isinstance(seller, str):
        fields["seller_name"] = seller

    shipping = {
        "shipping_delivery": record.get("delivery") or record.get("fastestDelivery"),
        "shipping_cost": to_float(record.get("shippingPrice")),
        "returns": record.get("returnPolicy"),
    }
    fields.update({k: v for k, v in shipping.items() if v not in (None, "", [], {})})
    if record.get("priceRange"):
        fields["price_range"] = record["priceRange"]

    metadata = dict(fields.get("metadata") or {})
    for key in ("condition", "bestsellerRanks", "isAmazonChoice", "amazonChoiceText",
                "monthlyPurchaseVolume", "answeredQuestions", "starsBreakdown",
                "locationText", "sustainabilityFeatures", "importantInformation"):
        if record.get(key) not in (None, "", [], {}):
            metadata[key] = record[key]
    if metadata:
        fields["metadata"] = metadata
    return fields


def map_temu(record: dict) -> dict[str, Any]:
    """apivault_labs/temu-product-scraper — the actor's `currency` field is
    unreliable (says USD while priceText reads 'MAD 39.99'), so the currency is
    parsed out of the printed price instead."""
    fields = map_generic(record)
    price_text = record.get("priceText") or record.get("priceLocal")
    amount, currency = parse_price(price_text)
    fields.update({k: v for k, v in {
        "price_amount": to_float(record.get("priceLocal")) or amount,
        "price_currency": currency,          # from the text, not record['currency']
        "price_text": price_text if isinstance(price_text, str) else None,
        "price_original": to_float(record.get("originalPrice")),
        "price_discount_percent": to_float(record.get("discount")),
        "rating_count": to_int(record.get("reviewsCountInt") or record.get("reviewsCount")),
        "seller_name": record.get("shopName"),
        "shipping_delivery": record.get("shippingText"),
        "shipping_free": record.get("freeShipping"),
    }.items() if v not in (None, "", [], {})})
    if record.get("soldCount"):
        fields.setdefault("metadata", {})["sold_count"] = record["soldCount"]
    return fields


def map_walmart(record: dict) -> dict[str, Any]:
    """e-commerce/walmart-product-detail-scraper — Walmart's own item payload:
    money lives in `priceInfo` as display strings, ids are split across
    usItemId/productId/upc/model."""
    fields = map_generic(record)
    price_info = record.get("priceInfo") if isinstance(record.get("priceInfo"), dict) else {}
    amount, currency = parse_price(price_info.get("price") or price_info.get("priceDisplay"))
    original, _ = parse_price(price_info.get("wasPrice"))

    fields.update({k: v for k, v in {
        "title": record.get("name"),
        "description": record.get("shortDescription") or record.get("description"),
        "price_amount": amount,
        "price_currency": currency,
        "price_original": original,
        "price_text": price_info.get("priceDisplay") or price_info.get("price"),
        "price_range": price_info.get("priceRange"),
        "availability_text": record.get("availability"),
        "images": [record.get("thumbnailUrl")] + list(record.get("allImages") or []),
        "rating_value": to_float(record.get("averageRating")),
        "rating_count": to_int(record.get("numberOfReviews")),
        "sku": record.get("usItemId") or record.get("productId"),
        "category": record.get("ironbankCategory"),
        "seller_name": record.get("sellerName"),
        "seller_rating": to_float(record.get("sellerAverageRating")),
        "seller_review_count": to_int(record.get("sellerReviewCount")),
    }.items() if v not in (None, "", [], {})})

    identifiers = {k: record[k] for k in ("upc", "model", "manufacturerProductId",
                                          "usItemId", "productId") if record.get(k)}
    if identifiers:
        fields["identifiers"] = identifiers

    returns = record.get("returnPolicy")
    if isinstance(returns, dict):
        fields["returns"] = returns.get("returnPolicyText")
    elif isinstance(returns, str):
        fields["returns"] = returns
    if record.get("fulfillmentType"):
        fields["shipping_delivery"] = None  # the actor gives no ETA, only the channel
        fields.setdefault("metadata", {})["fulfillment"] = record["fulfillmentType"]

    metadata = dict(fields.get("metadata") or {})
    for key in ("badge", "isSponsored", "ratingCounts", "rhPath", "offerType",
                "shippingRestriction", "orderLimit"):
        if record.get(key) not in (None, "", [], {}):
            metadata[key] = record[key]
    if metadata:
        fields["metadata"] = metadata
    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


def map_ecommerce_tool(record: dict) -> dict[str, Any]:
    """apify/e-commerce-scraping-tool — schema.org-shaped: the commercial data
    sits in `offers`, and the site-specific detail in `additionalProperties`
    (condition, seller, itemSpecifics, aggregateRating)."""
    offers = record.get("offers") if isinstance(record.get("offers"), dict) else {}
    extra = record.get("additionalProperties") if isinstance(
        record.get("additionalProperties"), dict) else {}
    rating = extra.get("aggregateRating") if isinstance(
        extra.get("aggregateRating"), dict) else {}

    fields: dict[str, Any] = {
        "title": _text_of(record.get("name")),
        "description": _text_of(record.get("description")),
        "brand": _text_of(record.get("brand")) or extra.get("brand"),
        "price_amount": unwrap_number(offers.get("price")),
        "price_currency": offers.get("currency") or offers.get("priceCurrency"),
        "price_original": unwrap_number(offers.get("listPrice") or extra.get("listPrice")),
        "availability_text": _text_of(offers.get("availability")),
        "images": [record.get("image")] if record.get("image") else [],
        "rating_value": to_float(record.get("rating") or rating.get("ratingValue")),
        "rating_count": to_int(record.get("reviewCount") or rating.get("reviewCount")),
        "sku": extra.get("sku") or extra.get("mpn"),
        "seller_name": _text_of(extra.get("seller")),
        "shipping_cost": to_float(extra.get("shippingCost")),
    }
    identifiers = {k: extra[k] for k in ("gtin", "gtin8", "gtin12", "gtin13", "mpn", "sku")
                   if extra.get(k)}
    if identifiers:
        fields["identifiers"] = identifiers

    specifics = extra.get("itemSpecifics")
    if isinstance(specifics, dict):
        fields["specifications"] = {str(k): v for k, v in specifics.items() if v}
        # eBay/Walmart put the real brand and category in the specifics table.
        fields["brand"] = fields["brand"] or specifics.get("Brand")
        fields["category"] = specifics.get("Type") or specifics.get("Category")
    if extra.get("condition"):
        fields["metadata"] = {"condition": extra["condition"]}
    if isinstance(extra.get("shippingCost"), (int, float)):
        fields["shipping_free"] = extra["shippingCost"] == 0

    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


_MOTIF_ALIEXPRESS_ITEM = re.compile(
    r"^https?://(?:[\w-]+\.)*aliexpress\.(com|us|ru)(/[^?#]*item/[^?#]+)",
    re.IGNORECASE,
)
_MOTIF_ALIEXPRESS_HOTE = re.compile(
    r"^https?://(?:[\w-]+\.)*aliexpress\.(com|us|ru)\b", re.IGNORECASE
)
# L'identifiant d'un article AliExpress, tel qu'il apparaît dans les paramètres
# des pages qui n'en sont pas une : `productIds=1005012768742030:12000059308786738`
# (article:variante), `x_object_id:1005012768742030` niché dans `utparam-url`,
# `productId=...`. Douze chiffres au moins — un identifiant d'article en compte
# seize aujourd'hui, et le seuil évite d'attraper un identifiant de session.
_MOTIF_ALIEXPRESS_ID = re.compile(
    r"(?:productIds?|x_object_id|objectId)(?:%3A|%3D|[:=])(\d{12,20})",
    re.IGNORECASE,
)


def _aliexpress_canonical_url(url: str) -> str:
    """Rewrite an AliExpress URL to the product page the actor can scrape.

    TWO REWRITES, and the second is what this function existed without.

    **Country hosts.** `fr.`, `es.`, `pt.` … 302 to
    `www.aliexpress.com/item/<id>.html?gatewayAdapt=…`, and the actor comes back
    with an empty dataset from that redirect — the run SUCCEEDs with 0 items,
    which surfaces as a 502. The same item id on `www.` scrapes fine, so the host
    is normalised and the tracking query string (`spm`, `pdp_ext_f`, …) dropped.

    **Pages that are not product pages.** AliExpress hands out promotional URLs
    that carry the product id in a query parameter rather than in the path:

        /ssr/300000512/BundleDeals2?productIds=1005012768742030%3A12000059308786738…

    There is no `item/` in that path, so the first rewrite left it untouched and
    the whole promotional URL went to the actor, whose run ended in `FAILED`. The
    extraction then burned its entire 300-second budget and surfaced as a
    timeout — the most expensive way there is to say "wrong URL". The id is
    recovered from the query string and the canonical page rebuilt.

    An id found this way is not a promise that the product still exists: the
    actor may still answer "no product". But it answers it in seconds, with a
    message that names what is missing.

    Args:
        url: URL as received from the caller.

    Returns:
        The canonical product URL, or the URL unchanged when no id is found.
    """
    match = _MOTIF_ALIEXPRESS_ITEM.match(url)
    if match:
        # The TLD is kept: aliexpress.us is a separate storefront, not a
        # translation of .com, so rewriting it would scrape a different listing.
        tld, path = match.group(1).lower(), match.group(2)
        return f"https://www.aliexpress.{tld}{path}"

    hote = _MOTIF_ALIEXPRESS_HOTE.match(url)
    identifiant = _MOTIF_ALIEXPRESS_ID.search(url)
    if hote and identifiant:
        return f"https://www.aliexpress.{hote.group(1).lower()}/item/{identifiant.group(1)}.html"
    return url


def map_aliexpress(items: list[dict], url: str) -> dict[str, Any]:
    """nifty.codes/aliexpress-product-ariants-scraper — emits ONE ROW PER
    VARIANT, all carrying the same product-level columns, so the rows are
    folded back into a single product with a variants list."""
    rows = [r for r in items if isinstance(r, dict) and r.get("Product Title")]
    if not rows:
        return {}
    head = rows[0]

    sale_amount, currency = parse_price(head.get("Sale Price"))
    original_amount, _ = parse_price(head.get("Original Price"))

    images = [i.strip() for i in str(head.get("All Images") or "").split("||") if i.strip()]
    if head.get("Main Image"):
        images.insert(0, head["Main Image"])

    # "Special Features: Lightweight || Product Care Instructions: Machine wash"
    specs: dict[str, Any] = {}
    for chunk in str(head.get("Specifications") or "").split("||"):
        key, sep, value = chunk.partition(":")
        if sep and key.strip():
            specs[key.strip()] = value.strip()

    # One row per purchasable SKU. The actor labels only the first axis
    # (colour); the remaining axes stay as AliExpress option codes, so they are
    # kept verbatim in `attributes` rather than being guessed at.
    variants = []
    for row in rows:
        amount, variant_currency = parse_price(row.get("Sale Price"))
        variants.append({
            "name": (str(row.get("Properties") or "").split(":")[0].strip() or "option"),
            "value": row.get("Variant Display Name") or row.get("Properties"),
            "sku": row.get("Variant SKU ID"),
            "price": amount,
            "currency": variant_currency,
            "availability": row.get("Saleable Status"),
            "image": row.get("Variant Image"),
            "attributes": {
                "option_codes": row.get("Variant Attribute"),
                "stock": row.get("Stock Quantity"),
                "original_price": row.get("Original Price"),
            },
        })

    shipping_cost, shipping_currency = parse_price(head.get("Shipping Info"))
    delivery = " - ".join(filter(None, [head.get("Min Delivery Time"),
                                        head.get("Max Delivery Time")]))

    # The actor reports the category only as numeric ids ("Category Path":
    # "44/100000306/202230603/202231007"), which is unusable as a product
    # sheet's category. The spec table is where AliExpress states it in words,
    # same as eBay/Walmart above; nothing is inferred when it says none.
    category = next(
        (specs[key] for key in ("Category", "Product Type", "Type", "Style", "Set Type")
         if specs.get(key)),
        None,
    )

    fields = {
        "title": head.get("Product Title"),
        "brand": None if head.get("Brand") in (None, "Not Available") else head.get("Brand"),
        "category": category,
        "sku": head.get("Product ID"),
        "identifiers": {"aliexpress_product_id": head.get("Product ID"),
                        "category_id": head.get("Category ID")},
        "price_amount": sale_amount,
        "price_currency": currency,
        "price_original": original_amount,
        "price_text": head.get("Sale Price"),
        "availability_text": head.get("Saleable Status"),
        "stock_quantity": to_int(head.get("Stock")),
        "images": images,
        "rating_value": to_float(head.get("Product Rating")),
        "rating_count": to_int(head.get("Total Reviews")),
        "specifications": specs,
        "variants": variants,
        "seller_name": head.get("Store Name"),
        "seller_url": head.get("Store URL"),
        "seller_rating": to_float(head.get("Store Rating")),
        "seller_location": head.get("Seller Country"),
        "shipping_cost": shipping_cost,
        "shipping_currency": shipping_currency,
        "shipping_free": shipping_cost == 0 if shipping_cost is not None else None,
        "shipping_delivery": delivery or None,
        "ships_from": head.get("Ships From"),
        "metadata": {
            "store_total_sales": head.get("Store Total Sales"),
            "store_open_since": head.get("Store Open Since"),
            "top_rated_seller": head.get("Top Rated Seller"),
            "total_available_skus": head.get("Total Available SKUs"),
            "sku_properties": head.get("SKU Properties"),
            "category_path": head.get("Category Path"),
        },
    }
    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


# ---------------------------------------------------------------------------
# 4. The registry
# ---------------------------------------------------------------------------
ACTOR_ADAPTERS: dict[str, ActorAdapter] = {
    "amazon": ActorAdapter(
        key="amazon",
        actor_id="junglee/Amazon-crawler",
        label="Amazon Product Scraper",
        build_input=lambda url: {
            "categoryOrProductUrls": [{"url": url}],
            "maxItemsPerStartUrl": 1,
            "scrapeProductDetails": True,
            "scrapeSellers": False,
            "maxOffers": 0,
            # Browse Amazon from the shopper's country: prices, "ships to" and
            # availability all change with the proxy location.
            "proxyCountry": TARGET_COUNTRY,
            "countryCode": TARGET_COUNTRY,
        },
        map_record=map_amazon,
        notes="Handles CAPTCHAs and geo pricing; accepts any /dp/ or /gp/product/ URL.",
    ),
    "temu": ActorAdapter(
        key="temu",
        actor_id="apivault_labs/temu-product-scraper",
        label="Temu Product Scraper",
        build_input=lambda url: {
            "productUrls": [url],
            "maxProductsPerKeyword": 1,
            "maxConcurrency": 1,
            "writeSummary": False,
        },
        map_record=map_temu,
        notes=("Temu redirects browsers to /login.html, so this is the only route. "
               "The actor exposes no country input — it returns the local currency "
               "of whatever storefront it reaches (MAD for temu.com/ma URLs), so "
               "pass a localized product URL when the currency matters."),
    ),
    "walmart": ActorAdapter(
        key="walmart",
        actor_id="e-commerce/walmart-product-detail-scraper",
        label="Walmart Product Detail Scraper",
        build_input=lambda url: {
            "startUrls": [{"url": url}],
            "maxProductsPerStartUrl": 1,
            "enqueueProductVariants": False,
        },
        map_record=map_walmart,
        notes="Free actor; also accepts Walmart search/category URLs.",
    ),
    "aliexpress": ActorAdapter(
        key="aliexpress",
        actor_id="nifty.codes/aliexpress-product-ariants-scraper",
        label="AliExpress Product & Variant Scraper",
        # One row per variant SKU — only pay for the extra rows when the caller
        # actually wants variants; row 1 already carries the product-level data.
        build_input=lambda url: {"urls": [_aliexpress_canonical_url(url)],
                                 "maxItems": 40 if INCLUDE_VARIANTS else 1},
        aggregate=map_aliexpress,
        notes=("Returns one row per variant, folded back into a single product. "
               "The official e-commerce tool returns empty records for AliExpress "
               "detail pages, so this dedicated actor is used instead. "
               "It has no country input and reports USD — use an aliexpress.com "
               "URL with the site's own currency parameters if you need MAD."),
    ),
    "generic_ecommerce": ActorAdapter(
        key="generic_ecommerce",
        actor_id="apify/e-commerce-scraping-tool",
        label="Apify E-commerce Scraping Tool",
        build_input=lambda url: {
            "scrapeMode": "AUTO",
            "detailsUrls": [{"url": url}],
            "maxProductResults": 1,
            "additionalProperties": True,
            # Country context for currency, shipping and availability.
            "countryCode": TARGET_COUNTRY.lower(),
        },
        map_record=map_ecommerce_tool,
        notes=("Official multi-marketplace actor (eBay, Shein, Target, Best Buy, "
               "hundreds more) — also the fallback when Playwright is blocked."),
    ),
}


def get_adapter(key: str) -> ActorAdapter:
    try:
        return ACTOR_ADAPTERS[key]
    except KeyError:
        raise KeyError(
            f"Unknown actor adapter {key!r}. Registered: {sorted(ACTOR_ADAPTERS)}"
        ) from None


def register_adapter(adapter: ActorAdapter) -> None:
    """Add an actor at runtime (pair with routing.register_domain)."""
    ACTOR_ADAPTERS[adapter.key] = adapter
