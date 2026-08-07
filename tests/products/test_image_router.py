"""POST /products/{product_id}/image -- Supabase Storage upload."""

import struct
import uuid
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from src.main import app
from src.products.config import products_settings
from src.products.constants import PNG_SIGNATURE
from src.products.models import Product
from src.products.storage import get_image_storage

from tests.conftest import FakeImageStorage

VALID_PAYLOAD = {
    "name": "Chaise de bureau ergonomique",
    "description": "Chaise a assise reglable, dossier maille.",
    "category": "Mobilier de bureau",
    "region": "FR",
}


def make_png() -> bytes:
    """Smallest valid 1x1 PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
async def product_id(client: AsyncClient) -> str:
    """Create a product sheet through the API and return its identifier."""
    response = await client.post("/products", json=VALID_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_upload_image(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: FakeImageStorage,
    product_id: str,
) -> None:
    png = make_png()

    response = await client.post(
        f"/products/{product_id}/image",
        files={"image": ("chaise.png", png, "image/png")},
    )

    assert response.status_code == 200, response.text
    image_url = response.json()["image_url"]
    assert image_url.endswith(f"{product_id}/chaise.png")
    assert products_settings.SUPABASE_STORAGE_BUCKET in image_url

    assert fake_storage.uploads == [(f"{product_id}/chaise.png", len(png), "image/png")]

    product = await db_session.get(Product, uuid.UUID(product_id))
    assert product is not None
    assert product.image_url == image_url


async def test_upload_rejects_non_image_file(
    client: AsyncClient, db_session: AsyncSession, product_id: str
) -> None:
    response = await client.post(
        f"/products/{product_id}/image",
        files={"image": ("not-an-image.png", b"just some text", "image/png")},
    )

    assert response.status_code == 422
    assert "JPEG" in response.json()["detail"]

    product = await db_session.get(Product, uuid.UUID(product_id))
    assert product is not None
    assert product.image_url is None


async def test_upload_rejects_oversized_image(
    client: AsyncClient, db_session: AsyncSession, product_id: str
) -> None:
    oversized = PNG_SIGNATURE + b"\x00" * products_settings.MAX_IMAGE_SIZE_BYTES

    response = await client.post(
        f"/products/{product_id}/image",
        files={"image": ("huge.png", oversized, "image/png")},
    )

    assert response.status_code == 413

    product = await db_session.get(Product, uuid.UUID(product_id))
    assert product is not None
    assert product.image_url is None


async def test_storage_failure_leaves_product_unchanged(
    client: AsyncClient, db_session: AsyncSession, product_id: str
) -> None:
    app.dependency_overrides[get_image_storage] = lambda: FakeImageStorage(fail=True)

    response = await client.post(
        f"/products/{product_id}/image",
        files={"image": ("chaise.png", make_png(), "image/png")},
    )

    assert response.status_code == 502

    product = await db_session.get(Product, uuid.UUID(product_id))
    assert product is not None
    assert product.image_url is None


async def test_upload_on_unknown_product(client: AsyncClient) -> None:
    response = await client.post(
        f"/products/{uuid.uuid4()}/image",
        files={"image": ("chaise.png", make_png(), "image/png")},
    )

    assert response.status_code == 404
