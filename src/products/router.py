"""API endpoints of the ``products`` domain. No business logic here: everything is
delegated to ``service.py``."""

from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status

from src.models import ErrorResponse
from src.products import service
from src.products.dependencies import DbSession, ExistingProduct, ImageStorage
from src.products.models import Product
from src.products.schemas import (
    ProductCreate,
    ProductExtractionRequest,
    ProductExtractionResponse,
    ProductResponse,
)

router = APIRouter(prefix="/products", tags=["products"])

ProductImage = Annotated[
    UploadFile, File(description="Product image (JPEG, PNG or WebP, 5 MB max).")
]


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product sheet",
    description=(
        "F1 -- Receives a product sheet, validates it and persists it in the `product` table. "
        "`image_url` may point at an already hosted image; to upload an image file instead, "
        "call `POST /products/{product_id}/image` afterwards.\n\n"
        "`region` is the country the market study will cover (ISO 3166-1 alpha-2, from the "
        "`EXTRACTION_ALLOWED_REGIONS` whitelist). Storing the sheet automatically starts a "
        "study for it (`STUDY_AUTO_START`); the study is then followed on `GET /studies`."
    ),
    responses={
        status.HTTP_201_CREATED: {"description": "Product sheet created"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Missing or invalid field",
        },
    },
)
async def create_product(payload: ProductCreate, db: DbSession) -> Product:
    return await service.create_product(db, payload)


@router.post(
    "/extract",
    response_model=ProductExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Extract a product sheet from an e-commerce URL and store it",
    description=(
        "Give a product page URL and the country the shopper is in; the extraction agent "
        "reads the page and the sheet it produces (name, description, category, image) is "
        "stored in the `product` table, exactly like a sheet typed by hand on "
        "`POST /products`.\n\n"
        "`region` is mandatory and is **never inferred** from the URL or its TLD: it pins "
        "the browser locale/timezone and the scraper's proxy country, which is what makes "
        "the price, the currency and the availability those of that shopper. It must be an "
        "ISO 3166-1 alpha-2 code from the configured whitelist "
        "(`EXTRACTION_ALLOWED_REGIONS`), and it is the value stored in `product.region`.\n\n"
        "**This request is slow: commonly 10 s to 2 min.** A real browser is started for "
        "ordinary shops, and a hosted scraper run is awaited for the marketplaces that "
        "block automation (Amazon, Temu, AliExpress, Walmart, eBay). Set `use_agent` to "
        "`false` for a deterministic extraction that makes no LLM call."
    ),
    responses={
        status.HTTP_201_CREATED: {"description": "Product extracted and stored"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": (
                "Invalid `url`; `region` missing, malformed, or outside the whitelist; or "
                "an extraction too incomplete to store a sheet (no name, description or "
                "category)"
            ),
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Extraction agent not configured, or unexpected server error",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": (
                "The page could not be loaded or rendered (network, timeout, anti-bot "
                "block), or the hosted scraper run failed"
            ),
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "model": ErrorResponse,
            "description": "The extraction exceeded `EXTRACTION_TIMEOUT_SECONDS`",
        },
    },
)
async def extract_product(
    payload: ProductExtractionRequest, db: DbSession
) -> ProductExtractionResponse:
    product, source_url, warnings = await service.extract_and_store_product(db, payload)
    return ProductExtractionResponse(
        product=ProductResponse.model_validate(product),
        source_url=source_url,
        warnings=warnings,
    )


@router.post(
    "/{product_id}/image",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload the image of a product sheet",
    description=(
        "Uploads an image file to Supabase Storage (JPEG/PNG/WebP, 5 MB max, verified against "
        "the file's magic bytes) and points the product sheet at its public URL. The product "
        "is only updated once the upload has succeeded."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Image stored, product sheet updated"},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No product sheet with this identifier",
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "model": ErrorResponse,
            "description": "Image larger than the maximum allowed size",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "File that is not a supported image",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": "Supabase Storage upload failed; the product was left unchanged",
        },
    },
)
async def upload_product_image(
    product: ExistingProduct,
    image: ProductImage,
    db: DbSession,
    storage: ImageStorage,
) -> Product:
    return await service.attach_image(db, product, image, storage)
