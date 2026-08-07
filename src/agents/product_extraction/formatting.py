"""
Output formatting
=================

The JSON schema is the product of this package, so serialization lives in one
place: `to_json` always emits every key — including the null ones — because a
stable shape is what makes the output easy to consume downstream.
`pretty_print` is the human view for the terminal.
"""

import json

from pydantic import BaseModel

from .schema import ProductData, ProductSummary


def to_dict(record: BaseModel) -> dict:
    return record.model_dump(mode="json")


def to_json(record: BaseModel, *, indent: int = 2) -> str:
    """Serialize a ProductSummary (the delivered five fields) or a full
    ProductData. Null keys are kept: a stable shape is what makes the output
    easy to consume downstream."""
    return json.dumps(to_dict(record), indent=indent, ensure_ascii=False)


def json_schema(full: bool = False) -> str:
    """The output contract itself — handy for documentation or validation."""
    model = ProductData if full else ProductSummary
    return json.dumps(model.model_json_schema(), indent=2)


def print_summary(summary: ProductSummary) -> None:
    line = "=" * 70
    print(f"\n{line}\n{summary.name or '(no name)'}\n{line}")
    print(f"Category:  {summary.category or '—'}")
    print(f"Image:     {summary.image_url or '—'}")
    print(f"Source:    {summary.source_url or '—'}")
    print(f"\n{summary.description or '(no description)'}\n")


def _money(product: ProductData) -> str:
    price = product.price
    if price.amount is None:
        return price.price_text or price.price_range or "—"
    text = f"{price.amount:g} {price.currency or ''}".strip()
    if price.original_amount:
        text += f" (was {price.original_amount:g}"
        text += f", -{price.discount_percent:g}%)" if price.discount_percent else ")"
    return text


def pretty_print(product: ProductData) -> None:
    line = "=" * 70
    print(f"\n{line}\n{product.title or '(no title)'}\n{line}")
    print(f"URL:          {product.final_url or product.url}")
    print(f"Source:       {product.source} ({product.source_domain})"
          f"{f' — viewed from {product.country}' if product.country else ''}")
    print(f"Price:        {_money(product)}")
    if product.price.price_range:
        print(f"Price range:  {product.price.price_range}")
    print(f"Availability: {product.availability or '—'}"
          f"{f' ({product.availability_text})' if product.availability_text else ''}")
    print(f"Brand:        {product.brand or '—'}")
    print(f"Category:     {product.category or '—'}"
          f"{'  |  ' + ' > '.join(product.categories) if product.categories else ''}")
    print(f"SKU:          {product.sku or '—'}")
    if product.identifiers:
        print(f"Identifiers:  {product.identifiers}")

    rating = product.rating
    if rating.value is not None or rating.review_count:
        print(f"Rating:       {rating.value if rating.value is not None else '—'}"
              f"/{rating.scale:g} from {rating.review_count or '—'} reviews")

    seller = product.seller
    if seller.name or seller.url:
        extra = f" ({seller.location})" if seller.location else ""
        print(f"Seller:       {seller.name or '—'}{extra}")

    shipping = product.shipping
    if any(v not in (None, [], "") for v in shipping.model_dump().values()):
        bits = []
        if shipping.free_shipping is not None:
            bits.append("free shipping" if shipping.free_shipping else "paid shipping")
        if shipping.cost is not None:
            bits.append(f"{shipping.cost:g} {shipping.currency or ''}".strip())
        if shipping.estimated_delivery:
            bits.append(shipping.estimated_delivery)
        if shipping.ships_from:
            bits.append(f"from {shipping.ships_from}")
        if shipping.returns:
            bits.append(shipping.returns)
        print(f"Shipping:     {' | '.join(bits) or '—'}")

    if product.promotions:
        print("Promotions:")
        for promo in product.promotions[:5]:
            label = promo.label or promo.description or ""
            code = f" [code: {promo.coupon_code}]" if promo.coupon_code else ""
            print(f"  - {label}{code}")

    if product.images:
        print(f"Images ({len(product.images)}):")
        for image in product.images[:5]:
            print(f"  - {image}")
        if len(product.images) > 5:
            print(f"  … {len(product.images) - 5} more")

    if product.variants:
        print(f"Variants ({len(product.variants)}):")
        for variant in product.variants[:10]:
            price = f" — {variant.price:g} {variant.currency or ''}".rstrip() if variant.price else ""
            print(f"  - {variant.name or 'option'}: {variant.value or '—'}{price}")
        if len(product.variants) > 10:
            print(f"  … {len(product.variants) - 10} more")

    if product.features:
        print("Features:")
        for feature in product.features[:6]:
            print(f"  - {feature[:120]}")

    if product.specifications:
        print(f"Specifications ({len(product.specifications)}):")
        for key, value in list(product.specifications.items())[:12]:
            print(f"  - {key}: {str(value)[:100]}")
        if len(product.specifications) > 12:
            print(f"  … {len(product.specifications) - 12} more")

    if product.description:
        print(f"\nDescription:\n  {product.description[:600]}"
              f"{'…' if len(product.description) > 600 else ''}")

    if product.metadata:
        print(f"\nMetadata:     {json.dumps(product.metadata, ensure_ascii=False, default=str)[:400]}")

    if product.warnings:
        print("\nWarnings:")
        for warning in product.warnings[:6]:
            print(f"  ! {warning}")
    print()
