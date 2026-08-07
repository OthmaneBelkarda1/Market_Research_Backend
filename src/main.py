"""FastAPI application: lifespan-managed shared clients and router registration."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

# `src.config` is imported before anything that reaches the extraction agent:
# `src/agents/product_extraction/config.py` runs `load_dotenv(override=True)` at import
# time, and materializing the project settings first keeps them deterministic.
# See `src/products/extraction.py` for the full explanation.
from src.config import Environment, settings
from src.database import SessionFactory, engine
from src.products.extraction import check_browser_available
from src.products.router import router as products_router
from src.studies import service as studies_service
from src.studies.router import router as studies_router
from src.studies.runner import check_pipeline_credentials

SHOW_DOCS_IN = {Environment.LOCAL, Environment.STAGING}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Own the process-wide resources: one HTTP client, one database engine."""
    # Warning only, never fatal: everything except a Playwright-routed extraction
    # works without a local browser, so a missing Chromium must not stop the boot.
    await check_browser_available()
    # Same rule for the pipeline credentials: one warning per missing key, no crash.
    check_pipeline_credentials()
    # Studies live in the memory of this worker. Whatever the database says, none of
    # them survived the previous process: they are closed before anything is served.
    async with SessionFactory() as session:
        await studies_service.recover_interrupted_studies(session)
    async with httpx.AsyncClient() as http_client:
        app.state.http_client = http_client
        yield
    await engine.dispose()


app_kwargs = {
    "title": "Agent IA d'Etude de Marche E-commerce",
    "description": (
        "Backend du pipeline d'etude de marche (iteration courante : socle, F1, "
        "extraction automatique d'une fiche produit depuis une URL e-commerce, et socle "
        "des etudes de marche -- execution du pipeline cablee au lot F8.2)."
    ),
    "version": "0.1.0",
    "lifespan": lifespan,
}
if settings.ENVIRONMENT not in SHOW_DOCS_IN:
    app_kwargs["openapi_url"] = None

app = FastAPI(**app_kwargs)

app.include_router(products_router)
app.include_router(studies_router)
