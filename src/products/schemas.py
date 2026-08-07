"""Pydantic schemas of the ``products`` domain."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AnyHttpUrl, ConfigDict, Field, StringConstraints, field_validator

from src.models import CustomModel
from src.products.config import products_settings
from src.products.constants import REGION_PATTERN

# Constraint aliases, so the request schema and any future form/dependency stay in sync.
ProductName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ProductDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]
ProductCategory = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]

# ISO 3166-1 alpha-2, uppercase. The whitelist itself is configuration, so membership is
# enforced by a validator rather than by a Literal type.
RegionCode = Annotated[str, StringConstraints(pattern=REGION_PATTERN)]


def normalize_region(value: object) -> object:
    """Uppercase a region code, then check membership of the configured whitelist.

    Runs *before* the pattern constraint so ``fr`` is accepted and stored as ``FR``; a
    value that is not two letters falls through to the pattern check.
    """
    if not isinstance(value, str):
        return value
    region = value.strip().upper()
    looks_like_a_code = len(region) == 2 and region.isalpha()
    if looks_like_a_code and region not in products_settings.allowed_regions:
        raise ValueError(
            f"Region {region!r} is not allowed. "
            f"Accepted values: {', '.join(products_settings.sorted_allowed_regions)}."
        )
    return region


class ProductCreate(CustomModel):
    """Product sheet submitted by the user (F1)."""

    name: ProductName = Field(description="Commercial name of the product.")
    description: ProductDescription = Field(description="Product description.")
    category: ProductCategory = Field(description="Product category.")
    region: RegionCode = Field(
        description=(
            "Market the product sheet targets: a country, as an ISO 3166-1 alpha-2 code "
            "from the configured whitelist (`EXTRACTION_ALLOWED_REGIONS`). It is the "
            "region the market study is run for -- prices, currency, competitors and ad "
            "libraries are all country-scoped -- and it is never inferred."
        )
    )
    image_url: AnyHttpUrl | None = Field(
        default=None,
        description=(
            "URL of an existing product image. Leave empty to upload a file afterwards on "
            "`POST /products/{product_id}/image`."
        ),
    )

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_and_check_region(cls, value: object) -> object:
        return normalize_region(value)

    # Merged with ``CustomModel.model_config`` by Pydantic; only the example is added here.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Chaise de bureau ergonomique",
                    "description": "Chaise a assise reglable, dossier maille respirant.",
                    "category": "Mobilier de bureau",
                    "region": "FR",
                    "image_url": "https://example.com/chaise.png",
                }
            ]
        }
    )


class ProductResponse(CustomModel):
    """Persisted product sheet."""

    id: uuid.UUID
    name: str
    description: str
    category: str
    region: str
    image_url: AnyHttpUrl | None = None
    created_at: datetime
    updated_at: datetime


class ProductExtractionRequest(CustomModel):
    """A product URL to extract, and the country it must be seen from."""

    url: AnyHttpUrl = Field(description="URL of the product page to extract.")
    region: RegionCode = Field(
        description=(
            "Shopper country the extraction is performed from (ISO 3166-1 alpha-2). "
            "Mandatory and never inferred from the URL: it pins the browser "
            "locale/timezone and the scraper's proxy country, which is what makes the "
            "price, the currency and the availability those of that shopper."
        )
    )
    use_agent: bool = Field(
        default=True,
        description=(
            "True: the LLM normalizes the extracted fields (requires OPENAI_API_KEY). "
            "False: deterministic extraction only -- no LLM call, no token cost."
        ),
    )

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_and_check_region(cls, value: object) -> object:
        return normalize_region(value)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "url": (
                        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
                    ),
                    "region": "MA",
                    "use_agent": False,
                }
            ]
        }
    )


class ProductExtractionResponse(CustomModel):
    """The stored product sheet, plus what the extraction had to report about itself."""

    product: ProductResponse = Field(description="The row created in the `product` table.")
    source_url: str = Field(description="The product page the sheet was extracted from.")
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal problems met during the extraction (product not shippable to the "
            "region, fallback to another scraper, no image found...). Empty most of the time."
        ),
    )
