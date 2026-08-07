"""Business logic of the ``products`` domain.

Two ways in, one table:

* **F1** -- ``create_product``: a product sheet typed by a human is validated and stored.
* **Extraction** -- ``extract_and_store_product``: only a URL and a region are given, the
  agent of ``src/agents/product_extraction`` reads the page, and the sheet it returns is
  stored the same way.

Agents are called from this service layer only -- never from ``router.py``. The call
itself lives in ``extraction.py``, which is the single module importing the agent.

Both entry points end on the same hook: a stored product sheet automatically starts a
market study (``STUDY_AUTO_START``). That hook can never fail the registration -- the
product row is already committed when it runs.
"""

import logging
import re
from pathlib import PurePosixPath

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.products import extraction
from src.products.config import products_settings
from src.products.constants import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    DEFAULT_IMAGE_SLUG,
    GENERIC_CONTENT_TYPES,
    IMAGE_READ_CHUNK_SIZE,
    JPEG_SIGNATURE,
    MAX_IMAGE_SLUG_LENGTH,
    PNG_SIGNATURE,
    WEBP_FORMAT_SIGNATURE,
    WEBP_RIFF_SIGNATURE,
    ErrorCode,
)
from src.products.exceptions import ExtractionIncomplete, ImageTooLarge, UnsupportedImageType
from src.products.models import Product
from src.products.schemas import ProductCreate, ProductExtractionRequest
from src.products.storage import ProductImageStorage, build_public_image_url
from src.studies import service as studies_service
from src.studies.constants import StudyTrigger

logger = logging.getLogger(__name__)


async def create_product(db: AsyncSession, payload: ProductCreate) -> Product:
    """Persist a validated product sheet, start its market study, and return the row."""
    product = Product(
        name=payload.name,
        description=payload.description,
        category=payload.category,
        region=payload.region,
        image_url=str(payload.image_url) if payload.image_url is not None else None,
    )
    db.add(product)
    await db.commit()
    # Reload so the server-generated columns (id, created_at, updated_at) are populated.
    await db.refresh(product)
    await studies_service.create_study_for_product(
        db, product, trigger_source=StudyTrigger.PRODUCTS
    )
    return product


async def extract_and_store_product(
    db: AsyncSession, payload: ProductExtractionRequest
) -> tuple[Product, str, list[str]]:
    """URL + region -> the agent reads the page -> the sheet is stored.

    Returns the stored row, the URL it came from, and the agent's non-fatal warnings.

    The region is the one the caller sent, from beginning to end: it drives the extraction
    (browser locale/timezone, scraper proxy country) and it is what lands in
    ``product.region``. Nothing is ever inferred from the URL or its TLD.

    Anything the extraction could not produce is *not* invented. ``name``, ``description``
    and ``category`` are NOT NULL in the table, so a sheet missing any of them is refused
    with 422 rather than stored half-empty. The image is the only optional part: an
    extraction with no usable image still yields a row, with ``image_url`` left null.
    """
    url = str(payload.url)
    summary, warnings = await extraction.extract_product(
        url, payload.region, use_agent=payload.use_agent
    )

    missing = [
        field
        for field, value in (
            ("name", summary.name),
            ("description", summary.description),
            ("category", summary.category),
        )
        if not (value or "").strip()
    ]
    if missing:
        logger.warning(
            "Incomplete extraction url=%s region=%s missing=%s", url, payload.region, missing
        )
        raise ExtractionIncomplete(
            f"{ErrorCode.EXTRACTION_INCOMPLETE} Missing: {', '.join(missing)}."
        )

    if not summary.image_url:
        warnings.append("no product image was found on the page; image_url is empty")

    product = Product(
        name=summary.name,
        description=summary.description,
        category=summary.category,
        region=payload.region,
        # The remote image URL is stored as-is: `image_url` is already documented as
        # accepting an already hosted image (F1). Re-hosting it in Supabase Storage would
        # be a change to this single line, with no migration.
        image_url=summary.image_url,
        source_url=summary.source_url or url,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    logger.info(
        "Product extracted and stored id=%s region=%s source_url=%s",
        product.id,
        payload.region,
        url,
    )
    await studies_service.create_study_for_product(
        db, product, trigger_source=StudyTrigger.EXTRACTIONS
    )
    return product, product.source_url, warnings


async def attach_image(
    db: AsyncSession,
    product: Product,
    image: UploadFile,
    storage: ProductImageStorage,
) -> Product:
    """Upload ``image`` to Supabase Storage, then point the product at its public URL.

    The upload happens *before* the row is updated: a storage failure (502) leaves the
    product untouched instead of pointing it at a missing object.
    """
    content = await _read_image(image)
    content_type = _resolve_image_content_type(content, image.content_type)
    image_path = f"{product.id}/{_normalize_filename(image.filename, content_type)}"

    await storage.upload(path=image_path, content=content, content_type=content_type)

    product.image_url = build_public_image_url(image_path)
    await db.commit()
    await db.refresh(product)
    return product


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


async def _read_image(image: UploadFile) -> bytes:
    """Read the upload in chunks, refusing anything above the configured limit."""
    max_size = products_settings.MAX_IMAGE_SIZE_BYTES
    chunks: list[bytes] = []
    total = 0
    while chunk := await image.read(IMAGE_READ_CHUNK_SIZE):
        total += len(chunk)
        if total > max_size:
            raise ImageTooLarge()
        chunks.append(chunk)
    if total == 0:
        raise UnsupportedImageType()
    return b"".join(chunks)


def _detect_image_content_type(content: bytes) -> str | None:
    """Identify the image type from its binary signature, ignoring name and headers."""
    if content.startswith(PNG_SIGNATURE):
        return "image/png"
    if content.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    if content.startswith(WEBP_RIFF_SIGNATURE) and content[8:12] == WEBP_FORMAT_SIGNATURE:
        return "image/webp"
    return None


def _resolve_image_content_type(content: bytes, declared_content_type: str | None) -> str:
    """Cross-check the declared content type against the actual magic bytes."""
    detected = _detect_image_content_type(content)
    if detected is None:
        raise UnsupportedImageType()

    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if declared in GENERIC_CONTENT_TYPES:
        return detected
    if declared not in ALLOWED_IMAGE_CONTENT_TYPES or declared != detected:
        raise UnsupportedImageType()
    return detected


def _normalize_filename(filename: str | None, content_type: str) -> str:
    """Build a safe object name: slugified stem + extension of the detected type."""
    stem = PurePosixPath(filename or DEFAULT_IMAGE_SLUG).name.rsplit(".", 1)[0]
    slug = _SLUG_PATTERN.sub("-", stem.lower()).strip("-")[:MAX_IMAGE_SLUG_LENGTH].strip("-")
    return f"{slug or DEFAULT_IMAGE_SLUG}{ALLOWED_IMAGE_CONTENT_TYPES[content_type]}"
