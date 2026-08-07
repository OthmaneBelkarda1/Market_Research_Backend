"""F8.1 -- automatic study creation after a product sheet is registered.

Both registration paths are covered: `POST /products` (typed by hand) and
`POST /products/extract` (the extraction agent, faked -- nothing goes out).
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.agents.product_extraction import ProductData
from src.products import extraction
from src.products.models import Product
from src.studies import service as studies_service
from src.studies.config import studies_settings
from src.studies.constants import StudyStatus, StudyTrigger
from src.studies.models import Study

from tests.studies.conftest import ProductFactory

PRODUCT_PAYLOAD = {
    "name": "Ceinture lombaire double traction",
    "description": "Ceinture de soutien lombaire a double sangle de traction.",
    "category": "sante-bien-etre",
    "region": "MA",
}

BOOKS_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """One successful extraction, no network, no LLM."""

    async def extract(url: str, **options: Any) -> ProductData:
        return ProductData(
            url=url,
            name="A Light in the Attic",
            short_description="A poetry collection by Shel Silverstein.",
            category="Poetry",
            images=["https://books.toscrape.com/media/a-light.jpg"],
            warnings=[],
        )

    monkeypatch.setattr(extraction, "extract_product_data", extract)


async def _single_study(db_session: AsyncSession) -> Study | None:
    return (await db_session.execute(select(Study))).scalars().one_or_none()


async def test_products_path_starts_a_study(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post("/products", json=PRODUCT_PAYLOAD)

    assert response.status_code == 201, response.text
    study = await _single_study(db_session)
    assert study is not None
    assert study.product_id == uuid.UUID(response.json()["id"])
    assert study.region == "MA"
    assert study.status == StudyStatus.CREATED
    assert study.trigger_source == StudyTrigger.PRODUCTS


async def test_extraction_path_starts_a_study(
    client: AsyncClient, db_session: AsyncSession, fake_agent: None
) -> None:
    response = await client.post(
        "/products/extract", json={"url": BOOKS_URL, "region": "FR", "use_agent": False}
    )

    assert response.status_code == 201, response.text
    study = await _single_study(db_session)
    assert study is not None
    assert study.product_id == uuid.UUID(response.json()["product"]["id"])
    assert study.region == "FR"
    assert study.trigger_source == StudyTrigger.EXTRACTIONS


async def test_auto_start_disabled_creates_no_study(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(studies_settings, "AUTO_START", False)

    response = await client.post("/products", json=PRODUCT_PAYLOAD)

    assert response.status_code == 201, response.text
    assert await _single_study(db_session) is None


async def test_a_legacy_region_starts_no_study(
    db_session: AsyncSession, make_product: ProductFactory
) -> None:
    """Rows predating the studies domain hold display names: nothing is inferred."""
    product = await make_product(region="Ile-de-France")

    study = await studies_service.create_study_for_product(
        db_session, product, trigger_source=StudyTrigger.PRODUCTS
    )

    assert study is None
    assert await _single_study(db_session) is None


async def test_a_failing_hook_never_fails_the_registration(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Study:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(studies_service, "create_study", boom)

    with caplog.at_level("WARNING", logger=studies_service.__name__):
        response = await client.post("/products", json=PRODUCT_PAYLOAD)

    assert response.status_code == 201, response.text
    assert await _single_study(db_session) is None
    assert "Automatic study creation failed" in caplog.text
    # The product is really there: a study that cannot start is not a reason to lose it.
    product = await db_session.get(Product, uuid.UUID(response.json()["id"]))
    assert product is not None


async def test_a_running_study_blocks_the_automatic_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Re-registering the same product/region does not stack a second study."""
    first = await client.post("/products", json=PRODUCT_PAYLOAD)
    product_id = uuid.UUID(first.json()["id"])
    product = await db_session.get(Product, product_id)
    assert product is not None

    again = await studies_service.create_study_for_product(
        db_session, product, trigger_source=StudyTrigger.PRODUCTS
    )

    assert again is None
    assert (await _single_study(db_session)) is not None
