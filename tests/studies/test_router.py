"""F8.1 -- POST /studies, GET /studies, GET /studies/{study_id}."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.products.models import Product
from src.studies import service
from src.studies.config import studies_settings
from src.studies.constants import StudySource, StudySourceStatus, StudyStatus, StudyTrigger
from src.studies.models import Study, StudySourceData

from tests.studies.conftest import ProductFactory


# ---------------------------------------------------------------------------
# POST /studies
# ---------------------------------------------------------------------------
async def test_create_study(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    response = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["product_id"] == str(product.id)
    assert body["region"] == "MA"
    assert body["status"] == StudyStatus.CREATED
    assert body["trigger_source"] == StudyTrigger.MANUAL
    assert body["progress"] == {}
    assert body["error"] is None
    assert body["started_at"] is None and body["finished_at"] is None

    study = await db_session.get(Study, uuid.UUID(body["id"]))
    assert study is not None
    assert study.product_id == product.id
    assert study.status == StudyStatus.CREATED
    assert study.trigger_source == StudyTrigger.MANUAL


async def test_created_study_stays_in_created(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    """``launch_study`` is a stub in this lot: nothing is simulated, no status moves."""
    response = await client.post("/studies", json={"product_id": str(product.id)})
    study_id = uuid.UUID(response.json()["id"])

    study = await db_session.get(Study, study_id)
    assert study is not None
    assert study.status == StudyStatus.CREATED
    assert study.started_at is None


async def test_region_falls_back_to_the_product(
    client: AsyncClient, make_product: ProductFactory
) -> None:
    product = await make_product(region="ES")

    response = await client.post("/studies", json={"product_id": str(product.id)})

    assert response.status_code == 202, response.text
    assert response.json()["region"] == "ES"


async def test_unknown_product_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.post("/studies", json={"product_id": str(uuid.uuid4())})

    assert response.status_code == 404
    assert (await db_session.execute(select(Study))).first() is None


async def test_no_region_anywhere_is_refused(
    client: AsyncClient, db_session: AsyncSession, make_product: ProductFactory
) -> None:
    """A product sheet holding a display name, not a country: nothing is guessed from it."""
    product = await make_product(region="Ile-de-France")

    response = await client.post("/studies", json={"product_id": str(product.id)})

    assert response.status_code == 422
    assert (await db_session.execute(select(Study))).first() is None


async def test_region_outside_the_whitelist_is_refused(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    response = await client.post("/studies", json={"product_id": str(product.id), "region": "ZZ"})

    assert response.status_code == 422
    message = response.text
    for allowed in studies_settings.sorted_allowed_regions:
        assert allowed in message
    assert (await db_session.execute(select(Study))).first() is None


@pytest.mark.parametrize("region", ["ZZZ", "1", "", "F"])
async def test_malformed_region_is_refused(
    client: AsyncClient, product: Product, region: str
) -> None:
    response = await client.post("/studies", json={"product_id": str(product.id), "region": region})

    assert response.status_code == 422


async def test_lowercase_region_is_normalized(client: AsyncClient, product: Product) -> None:
    response = await client.post("/studies", json={"product_id": str(product.id), "region": "ma"})

    assert response.status_code == 202, response.text
    assert response.json()["region"] == "MA"


async def test_langue_is_optional_and_lowercased(client: AsyncClient, product: Product) -> None:
    response = await client.post(
        "/studies", json={"product_id": str(product.id), "region": "MA", "langue": "FR"}
    )

    assert response.status_code == 202, response.text
    assert response.json()["langue"] == "fr"


# ---------------------------------------------------------------------------
# Duplicate lock
# ---------------------------------------------------------------------------
async def test_second_study_on_a_running_one_is_refused(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    first = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})
    assert first.status_code == 202

    second = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["study_id"] == first.json()["id"]
    assert len((await db_session.execute(select(Study))).all()) == 1


async def test_a_finished_study_does_not_block_a_new_one(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    first = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})
    study = await db_session.get(Study, uuid.UUID(first.json()["id"]))
    assert study is not None
    await service.set_study_status(db_session, study, StudyStatus.FAILED)

    second = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})

    assert second.status_code == 202, second.text
    assert second.json()["id"] != first.json()["id"]


async def test_another_region_is_not_blocked(client: AsyncClient, product: Product) -> None:
    """The lock is per (product, region): the same product may be studied elsewhere."""
    await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})

    other = await client.post("/studies", json={"product_id": str(product.id), "region": "FR"})

    assert other.status_code == 202, other.text


# ---------------------------------------------------------------------------
# GET /studies
# ---------------------------------------------------------------------------
async def test_list_is_filtered_sorted_and_paginated(
    client: AsyncClient, db_session: AsyncSession, make_product: ProductFactory
) -> None:
    product = await make_product()
    other_product = await make_product(name="Tapis de yoga")
    created = []
    for region in ("MA", "FR", "ES"):
        response = await client.post(
            "/studies", json={"product_id": str(product.id), "region": region}
        )
        created.append(response.json()["id"])
    await client.post("/studies", json={"product_id": str(other_product.id), "region": "MA"})

    listed = await client.get("/studies", params={"product_id": str(product.id)})

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 3
    assert body["limit"] == 20 and body["offset"] == 0
    # Newest first.
    assert [item["id"] for item in body["items"]] == list(reversed(created))

    page = await client.get(
        "/studies", params={"product_id": str(product.id), "limit": 2, "offset": 1}
    )
    assert page.json()["total"] == 3
    assert [item["id"] for item in page.json()["items"]] == list(reversed(created))[1:3]

    # Status filter: one of them is moved out of `created`.
    study = await db_session.get(Study, uuid.UUID(created[0]))
    assert study is not None
    await service.set_study_status(db_session, study, StudyStatus.FAILED)

    failed = await client.get("/studies", params={"status": "failed"})
    assert [item["id"] for item in failed.json()["items"]] == [created[0]]


async def test_list_rejects_an_out_of_bounds_limit(client: AsyncClient) -> None:
    assert (await client.get("/studies", params={"limit": 0})).status_code == 422
    assert (await client.get("/studies", params={"limit": 101})).status_code == 422
    assert (await client.get("/studies", params={"offset": -1})).status_code == 422


# ---------------------------------------------------------------------------
# GET /studies/{study_id}
# ---------------------------------------------------------------------------
async def test_get_study(client: AsyncClient, product: Product) -> None:
    created = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})

    response = await client.get(f"/studies/{created.json()['id']}")

    assert response.status_code == 200, response.text
    assert response.json() == created.json()


async def test_get_unknown_study(client: AsyncClient) -> None:
    response = await client.get(f"/studies/{uuid.uuid4()}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /studies/{study_id}/sources -- and /sources/{source}
# ---------------------------------------------------------------------------
async def _study_with_sources(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> str:
    """A study carrying the three outcomes a collector can end on."""
    created = await client.post("/studies", json={"product_id": str(product.id), "region": "MA"})
    study_id = uuid.UUID(created.json()["id"])
    db_session.add_all(
        [
            StudySourceData(
                study_id=study_id,
                source=StudySource.REDDIT,
                status=StudySourceStatus.SUCCEEDED,
                payload={"posts": [{"titre": "Douleurs lombaires au bureau"}]},
                exit_code=0,
                duration_seconds=12.5,
            ),
            StudySourceData(
                study_id=study_id,
                source=StudySource.AMAZON,
                status=StudySourceStatus.SKIPPED_REGION,
                exit_code=3,
                duration_seconds=0.4,
            ),
            StudySourceData(
                study_id=study_id,
                source=StudySource.META_ADS,
                status=StudySourceStatus.FAILED,
                error="ApifyApiError: actor run failed",
                exit_code=1,
                duration_seconds=3.0,
            ),
        ]
    )
    await db_session.commit()
    return str(study_id)


async def test_list_sources(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    study_id = await _study_with_sources(client, db_session, product)

    response = await client.get(f"/studies/{study_id}/sources")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    # Ordered by source name, so polling never reshuffles what was already returned.
    assert [item["source"] for item in body["items"]] == ["amazon", "meta_ads", "reddit"]
    assert [item["status"] for item in body["items"]] == ["skipped_region", "failed", "succeeded"]
    # The listing never carries the collected JSON.
    assert all("payload" not in item for item in body["items"])
    assert body["items"][1]["error"] == "ApifyApiError: actor run failed"
    assert body["items"][2]["exit_code"] == 0
    assert body["items"][2]["duration_seconds"] == 12.5


async def test_list_sources_of_a_study_that_has_not_collected_yet(
    client: AsyncClient, product: Product
) -> None:
    created = await client.post("/studies", json={"product_id": str(product.id)})

    response = await client.get(f"/studies/{created.json()['id']}/sources")

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0}


async def test_list_sources_of_an_unknown_study(client: AsyncClient) -> None:
    assert (await client.get(f"/studies/{uuid.uuid4()}/sources")).status_code == 404


async def test_get_one_source_returns_its_payload(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    study_id = await _study_with_sources(client, db_session, product)

    response = await client.get(f"/studies/{study_id}/sources/reddit")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["study_id"] == study_id
    assert body["source"] == "reddit"
    assert body["status"] == "succeeded"
    assert body["payload"] == {"posts": [{"titre": "Douleurs lombaires au bureau"}]}


async def test_get_a_failed_source_carries_the_error_and_no_payload(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    study_id = await _study_with_sources(client, db_session, product)

    response = await client.get(f"/studies/{study_id}/sources/meta_ads")

    assert response.status_code == 200, response.text
    assert response.json()["payload"] is None
    assert response.json()["error"] == "ApifyApiError: actor run failed"


async def test_get_a_source_that_has_not_run_yet(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    """404, not an empty payload: nothing was collected because nothing ran."""
    study_id = await _study_with_sources(client, db_session, product)

    response = await client.get(f"/studies/{study_id}/sources/google_trends")

    assert response.status_code == 404


async def test_get_an_unknown_source_name(
    client: AsyncClient, db_session: AsyncSession, product: Product
) -> None:
    study_id = await _study_with_sources(client, db_session, product)

    response = await client.get(f"/studies/{study_id}/sources/tiktok")

    assert response.status_code == 422
