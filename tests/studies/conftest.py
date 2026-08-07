"""Fixtures of the ``studies`` suite: real product rows, and the fake pipeline."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.products.models import Product
from src.studies import runner
from src.studies.config import studies_settings

ProductFactory = Callable[..., Awaitable[Product]]

FAKE_PIPELINE_ROOT = Path(__file__).parent / "fake_pipeline"


@pytest.fixture
def make_product(db_session: AsyncSession) -> ProductFactory:
    """Insert a product sheet directly, bypassing the API (and its automatic study)."""

    async def factory(*, region: str = "MA", name: str = "Ceinture lombaire") -> Product:
        product = Product(
            name=name,
            description="Ceinture de soutien lombaire a double sangle de traction.",
            category="sante-bien-etre",
            region=region,
        )
        db_session.add(product)
        await db_session.commit()
        await db_session.refresh(product)
        return product

    return factory


@pytest.fixture
async def product(make_product: ProductFactory) -> Product:
    return await make_product()


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, engine: AsyncEngine) -> Path:
    """Point the orchestrator at the fake pipeline and a throw-away workdir.

    ``STUDY_PIPELINE_ROOT`` is the whole substitution mechanism: the fake scripts sit at
    the very paths `constants.py` expects, so nothing about the wiring is bypassed -- the
    runner really spawns subprocesses, really parses their stdout, really reads their exit
    codes.

    The runner opens its own session (the request that created the study is long gone by
    then), so its ``SessionFactory`` is rebound to the test engine.
    """
    monkeypatch.setattr(studies_settings, "PIPELINE_ROOT", FAKE_PIPELINE_ROOT)
    monkeypatch.setattr(studies_settings, "WORKDIR", tmp_path / "studies")
    monkeypatch.setattr(
        runner, "SessionFactory", async_sessionmaker(engine, expire_on_commit=False)
    )
    # Rebuilt per test: a semaphore remembers the concurrency it was created with.
    monkeypatch.setattr(runner, "_semaphore", None)
    for name in [key for key in os.environ if key.startswith("FAKE_")]:
        monkeypatch.delenv(name)
    return tmp_path / "studies"
