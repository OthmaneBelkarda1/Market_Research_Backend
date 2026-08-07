"""Test fixtures: real PostgreSQL session (Supabase test database) + fake image storage.

The database is never mocked -- ``TEST_DATABASE_URL`` must point at a dedicated Supabase
database. Only Supabase Storage is replaced, through ``app.dependency_overrides``.
"""

import os
import uuid

# Captured BEFORE any `src` import, on purpose. Importing the application reaches
# `src/agents/product_extraction/config.py`, which runs `load_dotenv(override=True)` and
# overwrites `os.environ` with whatever `.env` holds. Without this snapshot,
# `TEST_DATABASE_URL=... uv run pytest` would be silently ignored in favour of the `.env`
# value -- and the suite would run against the wrong database.
_SHELL_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from src.database import get_db  # noqa: E402
from src.main import app  # noqa: E402
from src.models import Base  # noqa: E402
from src.products.exceptions import ImageUploadFailed  # noqa: E402
from src.products.models import Product  # noqa: E402
from src.products.storage import get_image_storage  # noqa: E402
from src.studies import service as studies_service  # noqa: E402
from src.studies.models import Study  # noqa: E402


class FakeImageStorage:
    """Records uploads instead of calling Supabase Storage."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.uploads: list[tuple[str, int, str]] = []

    async def upload(self, *, path: str, content: bytes, content_type: str) -> None:
        if self.fail:
            raise ImageUploadFailed()
        self.uploads.append((path, len(content), content_type))


@pytest.fixture(autouse=True)
def launched_studies(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Record ``launch_study`` calls instead of starting the pipeline. **Autouse.**

    Creating a study now really starts the orchestrator, which spawns the eleven modules
    of `src/agents/market_study` -- real Anthropic and Apify calls, tens of minutes, real
    money. No test may ever do that by accident, so every test gets the recorder and the
    tests that want an execution call ``runner._run_study`` explicitly, against the fake
    pipeline of `tests/studies/fake_pipeline`.
    """
    calls: list[uuid.UUID] = []

    async def record(study_id: uuid.UUID) -> None:
        calls.append(study_id)

    monkeypatch.setattr(studies_service, "launch_study", record)
    return calls


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The dedicated test database. A value exported in the shell wins over ``.env``."""
    url = _SHELL_TEST_DATABASE_URL or os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set (dedicated Supabase test database required).")
    return url


@pytest.fixture(scope="session")
async def engine(test_database_url: str) -> AsyncGenerator[AsyncEngine, None]:
    test_engine = create_async_engine(test_database_url, poolclass=NullPool)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        # Studies first: they reference ``product`` and the foreign key does not cascade
        # (deleting a product must never silently delete the studies made for it). The
        # three result tables do cascade from ``study``.
        await session.execute(delete(Study))
        await session.execute(delete(Product))
        await session.commit()


@pytest.fixture
def fake_storage() -> FakeImageStorage:
    return FakeImageStorage()


@pytest.fixture
async def client(
    db_session: AsyncSession, fake_storage: FakeImageStorage
) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_image_storage] = lambda: fake_storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()
