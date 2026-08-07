"""Real extractions -- these go out on the network.

Marked ``integration`` and excluded from the default run:

    uv run pytest -m integration

Both targets are free scraping sandboxes, rendered by a browser (no hosted scraper), and
called with ``use_agent=false`` so no OpenAI or Apify credit is ever spent. A local
Chromium is required: ``uv run playwright install chromium``.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from src.products.models import Product

pytestmark = pytest.mark.integration

TARGETS = [
    "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    "https://scrapeme.live/shop/Bulbasaur/",
]


@pytest.mark.parametrize("url", TARGETS)
async def test_extraction_against_a_real_site(
    client: AsyncClient, db_session: AsyncSession, url: str
) -> None:
    response = await client.post(
        "/products/extract", json={"url": url, "region": "MA", "use_agent": False}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_url"] == url

    product_body = body["product"]
    assert product_body["name"], "the agent returned no product name"
    assert product_body["description"], "the agent returned no description"
    assert product_body["category"], "the agent returned no category"
    assert product_body["region"] == "MA"

    product = await db_session.get(Product, uuid.UUID(product_body["id"]))
    assert product is not None
    assert product.region == "MA"
    assert product.source_url == url
