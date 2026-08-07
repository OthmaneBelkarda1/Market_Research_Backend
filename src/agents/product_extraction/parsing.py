"""
HTML parsing strategies
=======================

A rendered page goes through several INDEPENDENT extractors, from the most
structured/reliable to the most speculative:

    1. JsonLdExtractor      schema.org/Product in <script type=ld+json>
    2. ShopifyExtractor     the JSON blob every Shopify theme prints
    3. MicrodataExtractor   itemprop="..." attributes (older stores)
    4. OpenGraphExtractor   og:/product: meta tags
    5. MetaTagExtractor     twitter:, description, itemprop metas
    6. HtmlHeuristicExtractor   h1 / price-ish classes / breadcrumbs / gallery
    7. SpecificationExtractor   spec tables, definition lists, "key: value" rows

Each returns a flat dict (see `normalization`), and `merge_partials` keeps the
first non-empty value per key — so a site with perfect JSON-LD is read exactly,
while a site with none still yields a usable record from the heuristics.

Adding support for an odd HTML structure = adding one more class to
`DEFAULT_EXTRACTORS`; nothing else changes.
"""

import json
import re
from typing import Any, Protocol

from bs4 import BeautifulSoup

from .fetching import RenderedPage
from .normalization import parse_price, to_float, to_int

# ---------------------------------------------------------------------------
# 0. Shared helpers
# ---------------------------------------------------------------------------
_CURRENCY_CHARS = "$€£¥₹₽₩₺₪₦₫฿"
_PRICE_TEXT_RE = re.compile(
    rf"(?:[A-Z]{{3}}\s*)?[{_CURRENCY_CHARS}]\s*\d[\d.,\s]*|\d[\d.,\s]*\s*(?:[{_CURRENCY_CHARS}]|[A-Z]{{3}}\b)"
)
_RATING_RE = re.compile(r"(\d(?:[.,]\d)?)\s*(?:out of|/|sur)\s*5", re.I)
_REVIEWS_RE = re.compile(r"([\d.,]+\s*[KkMm]?\+?)\s*(?:customer\s+)?(?:reviews?|ratings?|avis|reseñas)", re.I)
_SKU_RE = re.compile(r"\b(?:SKU|Item(?:\s+model)?\s*(?:no|number|#)|Ref(?:erence)?|Art(?:icle)?\.?\s*no)\b[:\s#]*([A-Za-z0-9][\w./-]{2,40})", re.I)


def _txt(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _first_price_text(text: str) -> str | None:
    match = _PRICE_TEXT_RE.search(text or "")
    return match.group().strip() if match else None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _name_of(value: Any) -> str | None:
    """schema.org fields are string OR {'name': ...} OR a list of both."""
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            name = item.get("name") or item.get("legalName") or item.get("title")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


class SourceExtractor(Protocol):
    """Every strategy implements this; a failing one is skipped, never fatal."""

    name: str

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# 1. JSON-LD (schema.org/Product) — by far the most reliable source
# ---------------------------------------------------------------------------
class JsonLdExtractor:
    name = "json-ld"

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        product = self._find_product(soup)
        if not product:
            return {}

        fields: dict[str, Any] = {
            "title": product.get("name"),
            "description": product.get("description"),
            "brand": _name_of(product.get("brand")),
            "sku": product.get("sku") or product.get("productID") or product.get("mpn"),
            "category": _name_of(product.get("category")),
            "images": _as_list(product.get("image")),
        }

        identifiers = {
            key: product.get(key)
            for key in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "mpn", "isbn",
                        "productID", "model", "asin")
            if product.get(key)
        }
        if identifiers:
            fields["identifiers"] = identifiers

        # --- offers: price, currency, availability, seller, shipping --------
        offers = _as_list(product.get("offers"))
        # AggregateOffer wraps the real offers and carries the price range.
        for offer in list(offers):
            if isinstance(offer, dict) and "Aggregate" in str(offer.get("@type", "")):
                low, high = offer.get("lowPrice"), offer.get("highPrice")
                if low and high:
                    fields["price_range"] = f"{low} - {high}"
                fields.setdefault("price_amount", low or offer.get("price"))
                fields.setdefault("price_currency", offer.get("priceCurrency"))
                offers.extend(_as_list(offer.get("offers")))

        prices: list[float] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            spec = offer.get("priceSpecification")
            if isinstance(spec, dict):
                offer = {**spec, **{k: v for k, v in offer.items() if v is not None}}
            amount = to_float(offer.get("price"))
            if amount is not None:
                prices.append(amount)
                fields.setdefault("price_amount", amount)
            if offer.get("priceCurrency"):
                fields.setdefault("price_currency", offer["priceCurrency"])
            if offer.get("availability"):
                # 'https://schema.org/InStock' -> 'InStock'
                fields.setdefault("availability_text",
                                  str(offer["availability"]).rsplit("/", 1)[-1])
            if offer.get("itemCondition"):
                fields.setdefault("metadata", {})
                fields["metadata"]["condition"] = str(offer["itemCondition"]).rsplit("/", 1)[-1]
            seller = _name_of(offer.get("seller"))
            if seller:
                fields.setdefault("seller_name", seller)
            if offer.get("inventoryLevel"):
                level = offer["inventoryLevel"]
                fields.setdefault("stock_quantity",
                                  level.get("value") if isinstance(level, dict) else level)
            shipping = _as_list(offer.get("shippingDetails"))
            for detail in shipping:
                if not isinstance(detail, dict):
                    continue
                rate = detail.get("shippingRate")
                if isinstance(rate, dict):
                    cost = to_float(rate.get("value"))
                    if cost is not None:
                        fields.setdefault("shipping_cost", cost)
                        fields.setdefault("shipping_free", cost == 0)
                        fields.setdefault("shipping_currency", rate.get("currency"))
                dest = detail.get("shippingDestination")
                dest_name = _name_of(dest) or (
                    dest.get("addressCountry") if isinstance(dest, dict) else None
                )
                if isinstance(dest_name, dict):
                    dest_name = dest_name.get("name")
                if dest_name:
                    fields.setdefault("ships_to", dest_name)
        if len(prices) > 1 and "price_range" not in fields:
            fields["price_range"] = f"{min(prices)} - {max(prices)}"

        # --- rating ---------------------------------------------------------
        rating = product.get("aggregateRating")
        if isinstance(rating, dict):
            fields["rating_value"] = rating.get("ratingValue")
            fields["rating_scale"] = rating.get("bestRating") or 5
            fields["rating_count"] = rating.get("reviewCount") or rating.get("ratingCount")

        # --- specs & variants ----------------------------------------------
        specs: dict[str, Any] = {}
        for prop in _as_list(product.get("additionalProperty")):
            if isinstance(prop, dict) and prop.get("name"):
                specs[str(prop["name"])] = prop.get("value")
        for key in ("color", "size", "material", "weight", "width", "height", "depth",
                    "pattern", "audience", "countryOfOrigin"):
            value = product.get(key)
            if value:
                specs.setdefault(key, _name_of(value) or value)
        if specs:
            fields["specifications"] = specs

        variants = []
        for variant in _as_list(product.get("hasVariant")):
            if not isinstance(variant, dict):
                continue
            offer = next((o for o in _as_list(variant.get("offers")) if isinstance(o, dict)), {})
            variants.append({
                "name": "variant",
                "value": variant.get("name") or _name_of(variant.get("color")),
                "sku": variant.get("sku"),
                "price": offer.get("price"),
                "currency": offer.get("priceCurrency"),
                "availability": str(offer.get("availability", "")).rsplit("/", 1)[-1] or None,
                "image": (_as_list(variant.get("image")) or [None])[0],
            })
        if variants:
            fields["variants"] = variants

        return {k: v for k, v in fields.items() if v not in (None, "", [], {})}

    # -- locating the Product node ------------------------------------------
    def _find_product(self, soup: BeautifulSoup) -> dict | None:
        best: dict | None = None
        for script in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
            raw = script.string or script.get_text() or ""
            for node in self._walk(self._loads(raw)):
                types = {str(t).lower() for t in _as_list(node.get("@type"))}
                if types & {"product", "productgroup", "individualproduct", "vehicle"}:
                    # Prefer the node that actually carries offers.
                    if node.get("offers") or best is None:
                        best = node if node.get("offers") else best or node
        return best

    @staticmethod
    def _loads(raw: str) -> Any:
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Some themes emit trailing commas or several objects back to back.
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                try:
                    return json.JSONDecoder().raw_decode(cleaned)[0]
                except (json.JSONDecodeError, ValueError):
                    return None

    def _walk(self, node: Any, depth: int = 0):
        """Yield every dict in the document (@graph, nested arrays, mainEntity)."""
        if depth > 6:
            return
        if isinstance(node, dict):
            yield node
            for key in ("@graph", "mainEntity", "itemListElement", "hasVariant", "item"):
                for child in _as_list(node.get(key)):
                    yield from self._walk(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                yield from self._walk(child, depth + 1)


# ---------------------------------------------------------------------------
# 2. Shopify (covers a large share of independent stores)
# ---------------------------------------------------------------------------
class ShopifyExtractor:
    """Shopify themes print the whole product object into the page as JS.
    Prices there are integers in cents."""

    name = "shopify"
    _META_RE = re.compile(r"var\s+meta\s*=\s*(\{.*?\});\s*\n", re.S)
    _ANALYTICS_RE = re.compile(r"ShopifyAnalytics\.meta\s*=\s*(\{.*?\});", re.S)
    _PRODUCT_JSON_RE = re.compile(
        r'<script[^>]+type="application/json"[^>]*id="ProductJson[^"]*"[^>]*>(.*?)</script>', re.S
    )

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        product = self._product(page.html)
        if not product:
            return {}

        fields: dict[str, Any] = {
            "title": product.get("title"),
            "brand": product.get("vendor"),
            "category": product.get("type"),
            "metadata": {"shopify_product_id": product.get("id")},
        }
        variants_raw = product.get("variants") or []
        variants = []
        prices = []
        for variant in variants_raw:
            if not isinstance(variant, dict):
                continue
            price = variant.get("price")
            # Cents in the analytics blob, decimal string in ProductJson.
            amount = (price / 100) if isinstance(price, int) else to_float(price)
            if amount is not None:
                prices.append(amount)
            variants.append({
                "name": "option",
                "value": variant.get("public_title") or variant.get("name") or variant.get("title"),
                "sku": variant.get("sku"),
                "price": amount,
                "availability": ("in_stock" if variant.get("available") else "out_of_stock")
                if "available" in variant else None,
                "image": variant.get("featured_image", {}).get("src")
                if isinstance(variant.get("featured_image"), dict) else None,
            })
        if variants:
            fields["variants"] = variants
            first = variants_raw[0] if isinstance(variants_raw[0], dict) else {}
            if first.get("sku"):
                fields["sku"] = first["sku"]
        if prices:
            fields["price_amount"] = prices[0]
            if min(prices) != max(prices):
                fields["price_range"] = f"{min(prices)} - {max(prices)}"
        if product.get("currency") or product.get("price_currency"):
            fields["price_currency"] = product.get("currency") or product.get("price_currency")
        if product.get("images"):
            fields["images"] = product["images"]
        if product.get("description") or product.get("body_html"):
            html = product.get("description") or product["body_html"]
            fields["description"] = _txt(BeautifulSoup(html, "lxml"))
        if product.get("tags"):
            fields.setdefault("metadata", {})["tags"] = product["tags"]

        return {k: v for k, v in fields.items() if v not in (None, "", [], {})}

    def _product(self, html: str) -> dict | None:
        for pattern in (self._PRODUCT_JSON_RE, self._META_RE, self._ANALYTICS_RE):
            match = pattern.search(html)
            if not match:
                continue
            try:
                blob = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            product = blob.get("product") if isinstance(blob, dict) else None
            product = product or (blob if isinstance(blob, dict) and blob.get("variants") else None)
            if isinstance(product, dict):
                if isinstance(blob, dict) and blob.get("currency"):
                    product.setdefault("currency", blob["currency"])
                return product
        return None


# ---------------------------------------------------------------------------
# 3. Microdata (itemprop attributes)
# ---------------------------------------------------------------------------
class MicrodataExtractor:
    name = "microdata"
    _WANTED = {
        "name": "title", "description": "description", "sku": "sku",
        "brand": "brand", "price": "price_amount", "pricecurrency": "price_currency",
        "availability": "availability_text", "ratingvalue": "rating_value",
        "reviewcount": "rating_count", "ratingcount": "rating_count",
        "image": "images", "category": "category", "mpn": "sku", "gtin13": "sku",
    }

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        scope = soup.find(attrs={"itemtype": re.compile("schema.org/Product", re.I)}) or soup
        fields: dict[str, Any] = {}
        images: list[str] = []
        for node in scope.find_all(attrs={"itemprop": True})[:200]:
            prop = str(node.get("itemprop", "")).lower()
            key = self._WANTED.get(prop)
            if not key:
                continue
            value = (node.get("content") or node.get("datetime") or node.get("href")
                     or node.get("src") or _txt(node))
            value = (value or "").strip()
            if not value:
                continue
            if key == "images":
                images.append(value)
            elif key == "availability":
                fields.setdefault(key, value.rsplit("/", 1)[-1])
            else:
                fields.setdefault(key, value)
        if images:
            fields["images"] = images
        return fields


# ---------------------------------------------------------------------------
# 4. Open Graph / product meta tags
# ---------------------------------------------------------------------------
class OpenGraphExtractor:
    name = "open-graph"
    _MAP = {
        "og:title": "title", "og:description": "description",
        "product:price:amount": "price_amount", "og:price:amount": "price_amount",
        "product:price:currency": "price_currency", "og:price:currency": "price_currency",
        "product:original_price:amount": "price_original",
        "product:sale_price:amount": "price_amount",
        "product:availability": "availability_text", "og:availability": "availability_text",
        "product:brand": "brand", "og:brand": "brand",
        "product:retailer_item_id": "sku", "product:sku": "sku",
        "product:category": "category", "og:site_name": "_site_name",
    }

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        images: list[str] = []
        for tag in soup.find_all("meta"):
            prop = str(tag.get("property") or tag.get("name") or "").lower()
            content = (tag.get("content") or "").strip()
            if not prop or not content:
                continue
            if prop in ("og:image", "og:image:secure_url", "og:image:url"):
                images.append(content)
            elif prop in self._MAP:
                key = self._MAP[prop]
                if key == "_site_name":
                    fields.setdefault("metadata", {})["site_name"] = content
                else:
                    fields.setdefault(key, content)
        if images:
            fields["images"] = images
        return fields


# ---------------------------------------------------------------------------
# 5. Generic meta tags (twitter cards, plain description)
# ---------------------------------------------------------------------------
class MetaTagExtractor:
    name = "meta"

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        get = lambda selector: (soup.select_one(selector) or {}).get("content") if soup.select_one(selector) else None
        for selector, key in (
            ('meta[name="twitter:title"]', "title"),
            ('meta[name="twitter:description"]', "description"),
            ('meta[name="description"]', "description"),
            ('meta[itemprop="name"]', "title"),
            ('meta[itemprop="sku"]', "sku"),
            ('meta[itemprop="brand"]', "brand"),
        ):
            value = get(selector)
            if value and value.strip():
                fields.setdefault(key, value.strip())
        image = get('meta[name="twitter:image"]')
        if image:
            fields.setdefault("images", [image])
        return fields


# ---------------------------------------------------------------------------
# 6. HTML heuristics (sites with no structured data at all)
# ---------------------------------------------------------------------------
class HtmlHeuristicExtractor:
    name = "html-heuristics"

    _PRICE_ATTR_RE = re.compile(r"(^|[-_ ])price|amount|money", re.I)
    _OLD_PRICE_RE = re.compile(r"was|old|list|compare|strike|original|regular|rrp|msrp", re.I)
    _GALLERY_RE = re.compile(r"gallery|product.?image|main.?image|thumb|carousel|slider", re.I)
    _FEATURE_RE = re.compile(r"feature|bullet|highlight|key.?point", re.I)
    _CART_RE = re.compile(
        r"cart|basket|checkout|related|upsell|cross.?sell|recommend|also.?bought|"
        r"footer|header|nav|sidebar|widget", re.I
    )
    _CHROME_IMG_RE = re.compile(
        r"logo|sprite|icon|placeholder|pixel|spinner|loader|avatar|payment|visa|"
        r"mastercard|paypal|amex|badge|flag|banner|advert|tracking", re.I
    )

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        fields: dict[str, Any] = {}

        # --- title ----------------------------------------------------------
        heading = next((_txt(h) for h in soup.find_all("h1")[:3] if _txt(h)), "")
        if heading:
            fields["title"] = heading

        # --- prices ---------------------------------------------------------
        current, original = self._prices(soup)
        if current:
            fields["price_text"] = current
            amount, currency = parse_price(current)
            if amount is not None:
                fields["price_amount"] = amount
            if currency:
                fields["price_currency"] = currency
        if original:
            amount, _ = parse_price(original)
            if amount is not None:
                fields["price_original"] = amount

        # NB: no "% off" text heuristic here — page copy is full of unrelated
        # percentages (site-wide promos, related items). The discount is derived
        # from original vs current price in `normalization.build_product`, and
        # the LLM picks up genuine promotion wording.

        # --- images ---------------------------------------------------------
        images = self._images(soup)
        if images:
            fields["images"] = images

        # --- breadcrumbs -> categories --------------------------------------
        crumbs = self._breadcrumbs(soup)
        if crumbs:
            fields["categories"] = crumbs
            fields["category"] = crumbs[-1]

        # --- rating & reviews ------------------------------------------------
        head = page.text[:6000]
        rating = _RATING_RE.search(head)
        if rating:
            fields["rating_value"] = to_float(rating.group(1))
            fields["rating_text"] = rating.group().strip()
        reviews = _REVIEWS_RE.search(head)
        if reviews:
            fields["rating_count"] = to_int(reviews.group(1))

        # --- availability ----------------------------------------------------
        stock = re.search(
            r"(in stock|out of stock|sold out|unavailable|only \d+ left|pre-?order|backorder|"
            r"currently unavailable|temporarily out of stock)", page.text[:8000], re.I)
        if stock:
            fields["availability_text"] = stock.group(1)

        # --- sku --------------------------------------------------------------
        sku = _SKU_RE.search(page.text[:8000])
        if sku:
            fields["sku"] = sku.group(1)

        # --- features ---------------------------------------------------------
        features = self._features(soup)
        if features:
            fields["features"] = features

        # --- variant option groups --------------------------------------------
        variants = self._variants(soup)
        if variants:
            fields["variants"] = variants

        return fields

    # -- helpers -------------------------------------------------------------
    def _in_cart_widget(self, node) -> bool:
        """Mini-cart totals, 'related products' and upsell blocks print prices
        that are NOT this product's — walk up and reject them."""
        parent = node
        for _ in range(6):
            parent = getattr(parent, "parent", None)
            if parent is None or not getattr(parent, "get", None):
                break
            attrs = " ".join((parent.get("class") or []) + [parent.get("id") or ""])
            if self._CART_RE.search(attrs):
                return True
        return False

    def _candidates(self, soup: BeautifulSoup):
        """Elements whose class/id/data-* smells like a price."""
        for node in soup.find_all(["span", "div", "p", "strong", "b", "ins", "del", "s", "bdi"],
                                  limit=1200):
            attrs = " ".join(filter(None, [
                " ".join(node.get("class") or []), node.get("id") or "",
                node.get("data-testid") or "", node.get("itemprop") or "",
            ]))
            if self._PRICE_ATTR_RE.search(attrs):
                yield node, attrs

    def _prices(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        current = original = None
        for node, attrs in self._candidates(soup):
            text = _txt(node)
            if len(text) > 60 or self._in_cart_widget(node):
                continue
            price_text = _first_price_text(text)
            if not price_text:
                continue
            is_old = bool(self._OLD_PRICE_RE.search(attrs)) or node.name in ("del", "s")
            if is_old:
                original = original or price_text
            else:
                current = current or price_text
            if current and original:
                break
        if not current:  # last resort: first currency-looking string on the page
            node = soup.find(string=_PRICE_TEXT_RE)
            current = _first_price_text(str(node)) if node else None
        return current, original

    def _images(self, soup: BeautifulSoup) -> list[str]:
        """Two tiers: images that clearly belong to the product gallery first,
        then any other content image. Site chrome (logos, payment badges,
        spinners) is filtered out by filename."""
        gallery: list[str] = []
        other: list[str] = []
        for img in soup.find_all("img", limit=300):
            src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                   or img.get("data-original") or img.get("data-zoom-image") or "")
            if not src:
                srcset = img.get("srcset") or img.get("data-srcset") or ""
                src = srcset.split(",")[-1].strip().split(" ")[0] if srcset else ""
            if not src or self._CHROME_IMG_RE.search(src):
                continue

            # Class/id of the image AND its first few ancestors — galleries put
            # the meaningful class on a wrapper, not on the <img> itself.
            attrs = [" ".join(img.get("class") or []), img.get("id") or ""]
            parent = img.parent
            for _ in range(3):
                if parent is None or not getattr(parent, "get", None):
                    break
                attrs += [" ".join(parent.get("class") or []), parent.get("id") or ""]
                parent = parent.parent
            attrs_text = " ".join(filter(None, attrs))

            width = to_float(img.get("width")) or 0
            if (self._GALLERY_RE.search(attrs_text) or width >= 300
                    or "product" in src.lower()):
                gallery.append(src)
            elif (img.get("alt") or "").strip():
                other.append(src)
        return (gallery + other)[:25]

    def _breadcrumbs(self, soup: BeautifulSoup) -> list[str]:
        selectors = (
            '[class*="breadcrumb"] a', '[id*="breadcrumb"] a',
            'nav[aria-label*="readcrumb"] a', 'ol.breadcrumb li', '[itemtype*="BreadcrumbList"] a',
        )
        for selector in selectors:
            nodes = soup.select(selector)
            crumbs = [_txt(n) for n in nodes]
            crumbs = [c for c in crumbs if c and len(c) < 60 and c.lower() not in ("home", "accueil", "inicio")]
            if len(crumbs) >= 1:
                return crumbs[:8]
        return []

    def _features(self, soup: BeautifulSoup) -> list[str]:
        for container in soup.find_all(["ul", "div"], limit=400):
            attrs = " ".join((container.get("class") or []) + [container.get("id") or ""])
            if not self._FEATURE_RE.search(attrs):
                continue
            items = [_txt(li) for li in container.find_all("li", limit=25)]
            items = [i for i in items if 3 < len(i) < 300]
            if items:
                return items
        return []

    def _variants(self, soup: BeautifulSoup) -> list[dict]:
        """Read <select> option groups and swatch buttons (color/size pickers)."""
        variants: list[dict] = []
        for select in soup.find_all("select", limit=12):
            label = (select.get("name") or select.get("id") or select.get("aria-label") or "").strip()
            if not label or re.search(
                r"quantity|qty|country|currency|language|sort|rating|review|comment|"
                r"search|filter|newsletter", label, re.I
            ):
                continue
            for option in select.find_all("option", limit=40):
                value = _txt(option)
                if not value or value.lower() in ("choose an option", "select", "-"):
                    continue
                variants.append({
                    "name": re.sub(r"[-_]", " ", label).strip(),
                    "value": value,
                    "selected": option.has_attr("selected") or None,
                })
        for node in soup.select('[class*="swatch"] [title], [class*="swatch"] [aria-label]')[:30]:
            value = node.get("title") or node.get("aria-label")
            if value and len(value) < 40:
                variants.append({"name": "option", "value": value.strip()})
        return variants[:60]


# ---------------------------------------------------------------------------
# 7. Specification tables
# ---------------------------------------------------------------------------
class SpecificationExtractor:
    name = "specifications"
    _SKIP_PARENTS = {"nav", "header", "footer", "form", "aside"}
    _NOISE_RE = re.compile(r"^(add to|buy|sign in|log ?in|subscribe|share|cart|menu)\b", re.I)

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        specs: dict[str, Any] = {}

        # <table><tr><th>Key</th><td>Value</td>
        for row in soup.find_all("tr", limit=300):
            cells = row.find_all(["th", "td"], limit=3)
            if len(cells) < 2:
                continue
            key, value = _txt(cells[0]), _txt(cells[1])
            self._add(specs, key, value)

        # <dl><dt>Key</dt><dd>Value</dd>
        for definition in soup.find_all("dl", limit=40):
            terms = definition.find_all("dt")
            values = definition.find_all("dd")
            for term, value in zip(terms, values):
                self._add(specs, _txt(term), _txt(value))

        # <li>Key: Value</li> (only outside nav/footer chrome)
        for item in soup.find_all("li", limit=400):
            if any(parent.name in self._SKIP_PARENTS for parent in item.parents
                   if parent.name):
                continue
            text = _txt(item)
            if ":" not in text or len(text) > 200:
                continue
            key, _, value = text.partition(":")
            self._add(specs, key, value)

        return {"specifications": specs} if specs else {}

    def _add(self, specs: dict, key: str, value: str) -> None:
        key, value = key.strip(" : "), value.strip()
        if not key or not value or len(key) > 60 or len(value) > 400:
            return
        if key == value or self._NOISE_RE.search(key):
            return
        specs.setdefault(key, value)


# ---------------------------------------------------------------------------
# 8. Last resort
# ---------------------------------------------------------------------------
class DocumentTitleExtractor:
    """<title> as a title of last resort — it usually carries the shop name too
    ('Product — Shop'), so it must lose to the <h1> and to every meta tag."""

    name = "document-title"

    def extract(self, page: RenderedPage, soup: BeautifulSoup) -> dict[str, Any]:
        title = _txt(soup.title) if soup.title else (page.title or "")
        return {"title": title} if title else {}


# Priority order == merge order. Earlier extractors win conflicts.
DEFAULT_EXTRACTORS: tuple[SourceExtractor, ...] = (
    JsonLdExtractor(),
    ShopifyExtractor(),
    MicrodataExtractor(),
    OpenGraphExtractor(),
    MetaTagExtractor(),
    HtmlHeuristicExtractor(),
    SpecificationExtractor(),
    DocumentTitleExtractor(),
)


# Extractors reading data the page declares about itself. Their values are as
# exact as the site's own markup. Everything else is inference from layout and
# wording — usually right, occasionally fooled by an unrelated number on the
# page, so those values must stay overridable by the LLM (see `soft_fields`).
STRUCTURED_EXTRACTORS = frozenset({"json-ld", "shopify", "microdata", "open-graph", "meta"})


def parse_page(page: RenderedPage,
               extractors: tuple[SourceExtractor, ...] = DEFAULT_EXTRACTORS
               ) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Run every strategy. Returns ([(extractor name, fields)] in priority
    order, warnings). A strategy that raises is recorded and skipped — never
    fatal: one broken parser must not cost the whole extraction."""
    soup = BeautifulSoup(page.html, "lxml")
    partials: list[tuple[str, dict[str, Any]]] = []
    warnings: list[str] = []
    for extractor in extractors:
        try:
            result = extractor.extract(page, soup)
        except Exception as exc:                      # noqa: BLE001 - resilience by design
            warnings.append(f"{extractor.name} extractor failed: {type(exc).__name__}: {exc}")
            continue
        if result:
            partials.append((extractor.name, result))
    return partials, warnings
