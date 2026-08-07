"""F1 -- POST /products (JSON product sheet)."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.products.models import Product

VALID_PAYLOAD = {
    "name": "Chaise de bureau ergonomique",
    "description": "Chaise a assise reglable, dossier maille.",
    "category": "Mobilier de bureau",
    "region": "FR",
    "image_url": "https://example.com/chaise.png",
}


async def test_create_product(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post("/products", json=VALID_PAYLOAD)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]
    for field, value in VALID_PAYLOAD.items():
        assert body[field] == value

    product = await db_session.get(Product, uuid.UUID(body["id"]))
    assert product is not None
    assert product.name == VALID_PAYLOAD["name"]
    assert product.description == VALID_PAYLOAD["description"]
    assert product.category == VALID_PAYLOAD["category"]
    assert product.region == VALID_PAYLOAD["region"]
    assert product.image_url == VALID_PAYLOAD["image_url"]


async def test_create_product_without_optional_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = {key: VALID_PAYLOAD[key] for key in ("name", "description", "category", "region")}

    response = await client.post("/products", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["image_url"] is None

    product = await db_session.get(Product, uuid.UUID(body["id"]))
    assert product is not None
    assert product.image_url is None


async def test_create_product_missing_required_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/products", json={"description": VALID_PAYLOAD["description"]})

    assert response.status_code == 422
    missing = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "name") in missing
    assert ("body", "category") in missing
    assert ("body", "region") in missing

    assert (await db_session.execute(select(Product))).first() is None


async def test_create_product_rejects_invalid_image_url(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/products", json={**VALID_PAYLOAD, "image_url": "not-a-valid-url"}
    )

    assert response.status_code == 422
    assert any(error["loc"] == ["body", "image_url"] for error in response.json()["detail"])

    assert (await db_session.execute(select(Product))).first() is None


async def test_create_product_rejects_blank_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/products", json={**VALID_PAYLOAD, "name": "   "})

    assert response.status_code == 422
    assert (await db_session.execute(select(Product))).first() is None
