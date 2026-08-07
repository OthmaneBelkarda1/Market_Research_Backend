"""POST /products/extract -- the extraction agent is faked, nothing goes out."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.agents.product_extraction import (
    ActorRunError,
    ConfigError,
    PageLoadError,
    ProductData,
    UnsupportedUrlError,
)
from src.products import extraction
from src.products.config import products_settings
from src.products.constants import REGION_PROFILES, ErrorCode
from src.products.models import Product

BOOKS_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
VALID_PAYLOAD = {"url": BOOKS_URL, "region": "MA", "use_agent": False}

EXPECTED_NAME = "A Light in the Attic"
EXPECTED_DESCRIPTION = "A poetry collection by Shel Silverstein, priced at 51.77 GBP."
EXPECTED_CATEGORY = "Poetry"
EXPECTED_IMAGE = "https://books.toscrape.com/media/cache/fe/72/a-light.jpg"


def make_product(
    *,
    name: str | None = EXPECTED_NAME,
    description: str | None = EXPECTED_DESCRIPTION,
    category: str | None = EXPECTED_CATEGORY,
    image_url: str | None = EXPECTED_IMAGE,
    warnings: list[str] | None = None,
) -> ProductData:
    """A ``ProductData`` shaped the way ``summarize`` expects it.

    ``short_description`` is what feeds ``ProductSummary.description``.
    """
    return ProductData(
        url=BOOKS_URL,
        name=name,
        short_description=description,
        category=category,
        images=[image_url] if image_url else [],
        warnings=warnings or [],
    )


class AgentSpy:
    """Stands in for the agent's ``extract_product_data`` and records its calls."""

    def __init__(
        self, *, product: ProductData | None = None, error: Exception | None = None
    ) -> None:
        self.product = product if product is not None else make_product()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, url: str, **options: Any) -> ProductData:
        self.calls.append({"url": url, **options})
        if self.error is not None:
            raise self.error
        return self.product

    @property
    def last_call(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> AgentSpy:
    """Default spy: one successful extraction."""
    spy = AgentSpy()
    monkeypatch.setattr(extraction, "extract_product_data", spy)
    return spy


@pytest.fixture
def patch_agent(monkeypatch: pytest.MonkeyPatch) -> Callable[..., AgentSpy]:
    """Install a spy with custom behaviour (a specific product, an error to raise)."""

    def install(**kwargs: Any) -> AgentSpy:
        spy = AgentSpy(**kwargs)
        monkeypatch.setattr(extraction, "extract_product_data", spy)
        return spy

    return install


# ---------------------------------------------------------------------------
# Nominal flow
# ---------------------------------------------------------------------------
async def test_extract_stores_the_product(
    client: AsyncClient, db_session: AsyncSession, agent: AgentSpy
) -> None:
    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_url"] == BOOKS_URL
    assert body["warnings"] == []

    product_body = body["product"]
    assert product_body["name"] == EXPECTED_NAME
    assert product_body["description"] == EXPECTED_DESCRIPTION
    assert product_body["category"] == EXPECTED_CATEGORY
    assert product_body["image_url"] == EXPECTED_IMAGE
    assert product_body["created_at"] and product_body["updated_at"]

    product = await db_session.get(Product, uuid.UUID(product_body["id"]))
    assert product is not None
    assert product.name == EXPECTED_NAME
    assert product.description == EXPECTED_DESCRIPTION
    assert product.category == EXPECTED_CATEGORY
    assert product.image_url == EXPECTED_IMAGE
    assert product.source_url == BOOKS_URL
    # The region stored is the one the caller sent -- never one inferred from the URL,
    # whose TLD (.com) and content are British.
    assert product.region == "MA"


async def test_region_drives_the_extraction(client: AsyncClient, agent: AgentSpy) -> None:
    """The requested region reaches the agent as a browser identity, per request."""
    response = await client.post("/products/extract", json={**VALID_PAYLOAD, "region": "US"})

    assert response.status_code == 201, response.text
    locale, timezone, accept_language = REGION_PROFILES["US"]
    assert agent.last_call["locale"] == locale
    assert agent.last_call["timezone"] == timezone
    assert agent.last_call["accept_language"] == accept_language
    assert agent.last_call["use_agent"] is False
    # books.toscrape.com is rendered by a browser, so no hosted scraper is pinned.
    assert "force_actor" not in agent.last_call


async def test_marketplace_url_pins_the_regional_scraper(
    client: AsyncClient, agent: AgentSpy
) -> None:
    """An Apify-routed URL gets the clone of its actor that asks for the caller's region."""
    response = await client.post(
        "/products/extract",
        json={**VALID_PAYLOAD, "url": "https://www.amazon.com/dp/B0CX23V2ZK", "region": "FR"},
    )

    assert response.status_code == 201, response.text
    assert agent.last_call["force_actor"] == "amazon@FR"

    adapter = extraction.ACTOR_ADAPTERS["amazon@FR"]
    payload = adapter.build_input("https://www.amazon.com/dp/B0CX23V2ZK")
    assert payload["proxyCountry"] == "FR"
    assert payload["countryCode"] == "FR"


async def test_extraction_warnings_are_returned(client: AsyncClient, patch_agent) -> None:
    patch_agent(product=make_product(warnings=["no price shown: seller does not ship to MA"]))

    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == 201, response.text
    assert response.json()["warnings"] == ["no price shown: seller does not ship to MA"]


async def test_missing_image_is_a_warning_not_a_failure(
    client: AsyncClient, db_session: AsyncSession, patch_agent
) -> None:
    """An image that could not be found never costs the whole extraction."""
    patch_agent(product=make_product(image_url=None))

    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["product"]["image_url"] is None
    assert any("image" in warning for warning in body["warnings"])

    product = await db_session.get(Product, uuid.UUID(body["product"]["id"]))
    assert product is not None
    assert product.image_url is None


# ---------------------------------------------------------------------------
# Region validation
# ---------------------------------------------------------------------------
async def test_missing_region_is_rejected(
    client: AsyncClient, db_session: AsyncSession, agent: AgentSpy
) -> None:
    response = await client.post("/products/extract", json={"url": BOOKS_URL, "use_agent": False})

    assert response.status_code == 422
    assert ("body", "region") in {tuple(error["loc"]) for error in response.json()["detail"]}
    assert agent.calls == []
    assert (await db_session.execute(select(Product))).first() is None


async def test_region_outside_the_whitelist_is_rejected(
    client: AsyncClient, agent: AgentSpy
) -> None:
    response = await client.post("/products/extract", json={**VALID_PAYLOAD, "region": "ZZ"})

    assert response.status_code == 422
    message = response.text
    assert "ZZ" in message
    for allowed in products_settings.sorted_allowed_regions:
        assert allowed in message
    assert agent.calls == []


async def test_lowercase_region_is_normalized(
    client: AsyncClient, db_session: AsyncSession, agent: AgentSpy
) -> None:
    response = await client.post("/products/extract", json={**VALID_PAYLOAD, "region": "fr"})

    assert response.status_code == 201, response.text
    assert response.json()["product"]["region"] == "FR"

    product = await db_session.get(Product, uuid.UUID(response.json()["product"]["id"]))
    assert product is not None
    assert product.region == "FR"
    assert agent.last_call["locale"] == REGION_PROFILES["FR"][0]


@pytest.mark.parametrize("region", ["ZZZ", "1", "", "F"])
async def test_malformed_region_is_rejected(
    client: AsyncClient, agent: AgentSpy, region: str
) -> None:
    response = await client.post("/products/extract", json={**VALID_PAYLOAD, "region": region})

    assert response.status_code == 422
    assert agent.calls == []


async def test_invalid_url_is_rejected(client: AsyncClient, agent: AgentSpy) -> None:
    response = await client.post("/products/extract", json={**VALID_PAYLOAD, "url": "not-a-url"})

    assert response.status_code == 422
    assert ("body", "url") in {tuple(error["loc"]) for error in response.json()["detail"]}
    assert agent.calls == []


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (UnsupportedUrlError("nope"), 422, ErrorCode.UNSUPPORTED_URL),
        (PageLoadError("blocked"), 502, ErrorCode.PAGE_LOAD_FAILED),
        (ActorRunError("actor died"), 502, ErrorCode.ACTOR_RUN_FAILED),
        (ConfigError("APIFY_API_TOKEN is not set"), 500, ErrorCode.EXTRACTION_NOT_CONFIGURED),
        (RuntimeError("boom"), 500, ErrorCode.EXTRACTION_FAILED),
    ],
)
async def test_agent_errors_map_to_http_statuses(
    client: AsyncClient,
    db_session: AsyncSession,
    patch_agent,
    error: Exception,
    expected_status: int,
    expected_detail: ErrorCode,
) -> None:
    patch_agent(error=error)

    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == expected_status, response.text
    assert response.json()["detail"] == expected_detail
    assert (await db_session.execute(select(Product))).first() is None


async def test_a_missing_credential_never_leaks(client: AsyncClient, patch_agent) -> None:
    patch_agent(error=ConfigError("APIFY_API_TOKEN is not set - value apify_api_supersecret"))

    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == 500
    assert "apify_api_supersecret" not in response.text
    assert "APIFY_API_TOKEN" not in response.text


async def test_extraction_timeout_maps_to_504(
    client: AsyncClient, patch_agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(products_settings, "EXTRACTION_TIMEOUT_SECONDS", 0.05)

    async def never_finishes(url: str, **options: Any) -> ProductData:
        await asyncio.sleep(5)
        raise AssertionError("should have timed out")

    monkeypatch.setattr(extraction, "extract_product_data", never_finishes)

    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == 504, response.text
    assert response.json()["detail"] == ErrorCode.EXTRACTION_TIMEOUT


@pytest.mark.parametrize("missing_field", ["name", "category"])
async def test_incomplete_extraction_is_not_stored(
    client: AsyncClient, db_session: AsyncSession, patch_agent, missing_field: str
) -> None:
    """`name`, `description` and `category` are NOT NULL: nothing is invented for them.

    ``description`` is not parametrized because it cannot be missing on its own: when the
    agent returns no paragraph, ``summarize`` writes one from the extracted facts. It only
    ends up empty when the product has no name either -- which the ``name`` case covers.
    """
    patch_agent(product=make_product(**{missing_field: None}))

    response = await client.post("/products/extract", json=VALID_PAYLOAD)

    assert response.status_code == 422, response.text
    assert missing_field in response.json()["detail"]
    assert (await db_session.execute(select(Product))).first() is None


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
async def test_semaphore_bounds_concurrent_extractions(monkeypatch: pytest.MonkeyPatch) -> None:
    """More callers than slots must never run more extractions than the configured cap.

    Driven against ``extraction.extract_product`` rather than through the endpoint: the
    semaphore is what is under test, and going through HTTP would make every concurrent
    request share the single test session, which SQLAlchemy forbids (in production each
    request gets a session of its own).
    """
    state = {"running": 0, "peak": 0}

    async def slow_extraction(url: str, **options: Any) -> ProductData:
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        try:
            await asyncio.sleep(0.05)
            return make_product()
        finally:
            state["running"] -= 1

    monkeypatch.setattr(extraction, "extract_product_data", slow_extraction)
    cap = products_settings.EXTRACTION_MAX_CONCURRENCY

    results = await asyncio.gather(
        *(extraction.extract_product(BOOKS_URL, "MA", use_agent=False) for _ in range(cap * 3))
    )

    assert len(results) == cap * 3
    assert all(summary.name == EXPECTED_NAME for summary, _ in results)
    assert state["peak"] <= cap, f"{state['peak']} extractions ran at once for a cap of {cap}"
