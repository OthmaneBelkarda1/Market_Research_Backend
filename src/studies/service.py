"""Business logic of the ``studies`` domain.

Three ways in, one table: ``POST /studies`` (manual), and the two product registration
paths of the ``products`` domain, which call ``create_study_for_product``.

Every creation ends on the same single entry point -- ``launch_study`` -- which is a stub
in this lot: F8.1 owns the persistence and the lifecycle, F8.2 wires the pipeline behind
that one function without touching the routers or the hooks.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.products.models import Product
from src.studies.config import studies_settings
from src.studies.constants import (
    ACTIVE_RUNNING_STATUSES,
    ACTIVE_STUDY_STATUSES,
    RunErrorCode,
    StudySource,
    StudyStatus,
    StudyTrigger,
)
from src.studies.exceptions import (
    RegionRequired,
    StudyAlreadyRunning,
    StudyNotFound,
    StudyProductNotFound,
    StudyReportNotFound,
    StudyReportNotReplayable,
    StudySourceNotFound,
)
from src.studies.models import Study, StudyReport, StudySourceData
from src.studies.schemas import StudyCreate

logger = logging.getLogger(__name__)


def resolve_product_region(region: str | None) -> str | None:
    """The study region carried by a product sheet, or ``None`` if it carries none.

    ``product.region`` is free text at the database level (it predates the study domain and
    older rows hold values such as ``"Ile-de-France"``). A study needs a country: anything
    that is not an allowed ISO 3166-1 alpha-2 code yields ``None`` here, and no region is
    ever guessed from it.
    """
    candidate = (region or "").strip().upper()
    looks_like_a_code = len(candidate) == 2 and candidate.isalpha()
    if looks_like_a_code and candidate in studies_settings.allowed_regions:
        return candidate
    return None


async def create_study(
    db: AsyncSession,
    payload: StudyCreate,
    *,
    trigger_source: StudyTrigger = StudyTrigger.MANUAL,
) -> Study:
    """Create a study in status ``created`` and hand it to ``launch_study``.

    Raises 404 if the product does not exist, 422 if no region can be resolved, and 409 if
    a study is already running for this (product, region): a study costs ~2 $ of LLM
    credits and 30-60 min of Apify quota, so the accidental duplicate is the first
    operational risk of the whole feature.

    The lock is a read followed by a write, which a second worker could interleave. The
    application deliberately runs on a single uvicorn worker (see README), and F8.2 keeps
    that constraint for the orchestrator itself.
    """
    product = await db.get(Product, payload.product_id)
    if product is None:
        raise StudyProductNotFound()

    region = payload.region or resolve_product_region(product.region)
    if region is None:
        raise RegionRequired()

    active_id = await find_active_study_id(db, product_id=product.id, region=region)
    if active_id is not None:
        raise StudyAlreadyRunning(active_id)

    study = Study(
        product_id=product.id,
        region=region,
        langue=payload.langue,
        status=StudyStatus.CREATED.value,
        trigger_source=trigger_source.value,
        progress={},
    )
    db.add(study)
    await db.commit()
    # Reload so the server-generated columns (id, created_at, updated_at) are populated.
    await db.refresh(study)

    logger.info(
        "Study created id=%s product_id=%s region=%s langue=%s trigger=%s",
        study.id,
        study.product_id,
        study.region,
        study.langue,
        study.trigger_source,
    )
    await launch_study(study.id)
    return study


async def create_study_for_product(
    db: AsyncSession,
    product: Product,
    *,
    trigger_source: StudyTrigger,
) -> Study | None:
    """Automatic trigger: start a study for a product that has just been registered.

    Never raises. A study that cannot be created (no usable region, database hiccup, a
    study already running) must not turn a successful product registration into an error
    for the client -- the product row is already committed at this point.

    Returns the study, or ``None`` when none was created.
    """
    if not studies_settings.AUTO_START:
        logger.info("STUDY_AUTO_START is false: no study created for product_id=%s", product.id)
        return None

    region = resolve_product_region(product.region)
    if region is None:
        logger.info(
            "No study for product_id=%s: region %r is not an allowed ISO 3166-1 alpha-2 code",
            product.id,
            product.region,
        )
        return None

    # Read before anything may fail: the recovery below expires every ORM instance of the
    # session, and an expired attribute read outside a greenlet raises MissingGreenlet.
    product_id = product.id

    try:
        return await create_study(
            db,
            StudyCreate(product_id=product_id, region=region),
            trigger_source=trigger_source,
        )
    except Exception:
        # Includes StudyAlreadyRunning: an ongoing study for this (product, region) is a
        # perfectly good reason not to start a second one, not a reason to fail the caller.
        logger.warning(
            "Automatic study creation failed for product_id=%s region=%s; the product was "
            "registered anyway",
            product_id,
            region,
            exc_info=True,
        )
        await _recover_session(db, product)
        return None


async def _recover_session(db: AsyncSession, product: Product) -> None:
    """Roll the failed transaction back and give the caller a usable ``product`` again.

    A rollback expires every instance of the session. Without the reload, the caller would
    hand an expired ``Product`` to FastAPI, whose response serialization is synchronous:
    the lazy refresh it triggers raises ``MissingGreenlet`` and turns a successful
    registration into a 500 -- the very failure this hook exists to prevent.

    Both statements are guarded: if the database is genuinely unreachable, there is nothing
    left to recover, and raising here would defeat the purpose just as surely.
    """
    try:
        await db.rollback()
        await db.refresh(product)
    except Exception:
        logger.warning("Could not restore the session after a failed study creation", exc_info=True)


async def find_active_study_id(
    db: AsyncSession, *, product_id: uuid.UUID, region: str
) -> uuid.UUID | None:
    """Identifier of the study currently holding the (product, region) lock, if any."""
    statement = (
        select(Study.id)
        .where(
            Study.product_id == product_id,
            Study.region == region,
            Study.status.in_(ACTIVE_STUDY_STATUSES),
        )
        .order_by(Study.created_at.desc())
        .limit(1)
    )
    return await db.scalar(statement)


async def get_study(db: AsyncSession, study_id: uuid.UUID) -> Study:
    """Load a study, or raise 404."""
    study = await db.get(Study, study_id)
    if study is None:
        raise StudyNotFound()
    return study


async def list_studies(
    db: AsyncSession,
    *,
    product_id: uuid.UUID | None,
    status: StudyStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[Study], int]:
    """One page of the history, newest first, plus the total matching the filters."""
    filters = []
    if product_id is not None:
        filters.append(Study.product_id == product_id)
    if status is not None:
        filters.append(Study.status == status.value)

    page = (
        select(Study)
        .where(*filters)
        .order_by(Study.created_at.desc(), Study.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total = select(func.count()).select_from(Study).where(*filters)

    items = list((await db.scalars(page)).all())
    return items, await db.scalar(total) or 0


async def list_study_sources(db: AsyncSession, study_id: uuid.UUID) -> list[StudySourceData]:
    """Every collector row of a study, in a stable order.

    Not paginated: there are six collectors at most, and the unique constraint on
    (study_id, source) caps the result at six rows for good. Ordered by source name rather
    than by time, so polling this endpoint while the study runs never reshuffles the rows
    already returned -- collectors run concurrently and finish in no fixed order.
    """
    statement = (
        select(StudySourceData)
        .where(StudySourceData.study_id == study_id)
        .order_by(StudySourceData.source)
    )
    return list((await db.scalars(statement)).all())


async def get_study_source(
    db: AsyncSession, study_id: uuid.UUID, source: StudySource
) -> StudySourceData:
    """One collector row of a study, payload included, or raise 404.

    A missing row is a 404 and not an empty payload: the collector has not run yet (the
    study is still collecting, or never got there), which is a different thing from a
    collector that ran and returned nothing.
    """
    statement = select(StudySourceData).where(
        StudySourceData.study_id == study_id,
        StudySourceData.source == source.value,
    )
    row = await db.scalar(statement)
    if row is None:
        raise StudySourceNotFound()
    return row


async def get_study_report(db: AsyncSession, study_id: uuid.UUID) -> StudyReport:
    """The report of a study, or raise 404.

    A missing report is a 404 rather than an empty body: it means F7 has not run yet, or
    could not produce one -- and a study whose report fails is marked ``failed``, never
    ``completed``. So a study in ``completed`` or ``partial`` always has one here.
    """
    statement = select(StudyReport).where(StudyReport.study_id == study_id)
    report = await db.scalar(statement)
    if report is None:
        raise StudyReportNotFound()
    return report


async def set_study_status(
    db: AsyncSession,
    study: Study,
    status: StudyStatus,
    *,
    error: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> Study:
    """The single place where a study changes status.

    Keeping every transition here is what lets F8.2 grow a real state machine (progress
    updates, timestamps, restart recovery) without hunting for scattered ``UPDATE``s.
    ``updated_at`` is refreshed by the ORM's ``onupdate``.
    """
    study.status = status.value
    if error is not None:
        study.error = error
    if started_at is not None:
        study.started_at = started_at
    if finished_at is not None:
        study.finished_at = finished_at
    await db.commit()
    await db.refresh(study)
    logger.info("Study id=%s moved to status=%s", study.id, study.status)
    return study


async def replay_report(db: AsyncSession, study_id: uuid.UUID) -> Study:
    """Hand a study's report back to F7, without re-collecting or re-analysing.

    The study must exist and carry its F3 to F5 payloads. Everything else — a failed
    write-up, a report already on file — is replayable: a report that came out wrong is
    exactly what this endpoint is for, and the previous one is replaced only once the
    new one succeeds.

    Args:
        db: Database session.
        study_id: Study whose report is to be rebuilt.

    Returns:
        The study, back in status ``reporting``.

    Raises:
        StudyNotFound: No study with this identifier.
        StudyReportNotReplayable: An analysis required by F7 is missing.
    """
    from src.studies import runner

    study = await get_study(db, study_id)
    manquantes = await runner.analyses_manquantes(db, study_id)
    if manquantes:
        raise StudyReportNotReplayable(manquantes)

    await set_study_status(db, study, StudyStatus.REPORTING)
    await runner.launch_report_rebuild(study_id)
    logger.info("Study id=%s: report replay scheduled", study_id)
    return study


async def launch_study(study_id: uuid.UUID) -> None:
    """Single entry point of the pipeline execution.

    The orchestrator lives in ``runner.py``, the only module of the backend that knows the
    pipeline exists. It is imported here rather than at module level because the runner
    needs this service for its status transitions: the local import breaks the cycle and
    keeps that dependency one-way in the code that matters.

    Returns as soon as the background task is scheduled -- a study runs for 30 to 60
    minutes and is followed by polling ``GET /studies/{id}``.
    """
    from src.studies import runner

    await runner.launch_study(study_id)


async def recover_interrupted_studies(db: AsyncSession) -> int:
    """Fail every study left running by a previous process, and say why.

    Studies live in the memory of one uvicorn worker: a restart kills them, whatever the
    database still says. Leaving them in ``collecting`` would show a progress bar that can
    never move again, and would hold the (product, region) lock forever. They are marked
    ``failed`` with an explicit code instead, which makes them relaunchable through
    ``POST /studies``.

    Fine-grained resume (checkpoint and continue where the study stopped) is deliberately
    out of scope: everything already collected stays in the database, so a relaunch only
    ever costs what it re-collects.
    """
    statement = select(Study).where(Study.status.in_(ACTIVE_RUNNING_STATUSES))
    interrupted = list((await db.scalars(statement)).all())
    for study in interrupted:
        await set_study_status(
            db,
            study,
            StudyStatus.FAILED,
            error={
                "code": str(RunErrorCode.INTERRUPTED_BY_RESTART),
                "message": (
                    "The study was interrupted by a restart of the application. "
                    "Everything collected before the restart is kept; relaunch to resume."
                ),
            },
            finished_at=datetime.now(UTC),
        )
    if interrupted:
        logger.warning(
            "%d study(ies) interrupted by a restart marked as failed: %s",
            len(interrupted),
            ", ".join(str(study.id) for study in interrupted),
        )
    return len(interrupted)
