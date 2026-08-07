"""API endpoints of the ``studies`` domain. No business logic here: everything is
delegated to ``service.py``."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from src.models import ErrorResponse
from src.studies import service
from src.studies.constants import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT, StudySource, StudyStatus
from src.studies.dependencies import DbSession, ExistingStudy
from src.studies.models import Study, StudyReport, StudySourceData
from src.studies.schemas import (
    StudyConflictResponse,
    StudyCreate,
    StudyListResponse,
    StudyReportResponse,
    StudyResponse,
    StudySourceListResponse,
    StudySourceResponse,
    StudySourceSummary,
)

router = APIRouter(prefix="/studies", tags=["studies"])

ProductFilter = Annotated[
    uuid.UUID | None, Query(description="Keep only the studies of this product sheet.")
]
StatusFilter = Annotated[
    StudyStatus | None,
    # Aliased: the query parameter is `status`, but `status` is FastAPI's status codes here.
    Query(alias="status", description="Keep only studies in this status."),
]
ListLimit = Annotated[
    int, Query(ge=1, le=MAX_LIST_LIMIT, description="Maximum number of studies returned.")
]
ListOffset = Annotated[int, Query(ge=0, description="Number of studies skipped.")]
SourceName = Annotated[StudySource, Path(description="Collector whose data is read.")]


@router.post(
    "",
    response_model=StudyResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a market study for a product sheet",
    description=(
        "Creates a study for an existing product sheet and hands it to the pipeline.\n\n"
        "The response is returned immediately in status `created`: a study runs for 30 to "
        "60 minutes, so its progress is followed by polling `GET /studies/{study_id}`.\n\n"
        "`region` must be an ISO 3166-1 alpha-2 code from the configured whitelist "
        "(`STUDY_ALLOWED_REGIONS`). When it is omitted, the region of the product sheet is "
        "used; when neither carries one, the request is refused with 422 -- a region is "
        "never inferred, because it drives every collector and every price collected.\n\n"
        "**Execution is wired in lot F8.2**: in this lot the study is persisted and stays "
        "in status `created`."
    ),
    responses={
        status.HTTP_202_ACCEPTED: {"description": "Study created, in status `created`"},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No product sheet with this identifier",
        },
        status.HTTP_409_CONFLICT: {
            "model": StudyConflictResponse,
            "description": (
                "A study is already running for this (product, region). The identifier of "
                "that study is returned so it can be polled."
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": (
                "No region at all (neither in the request nor on the product sheet), or a "
                "region outside the whitelist"
            ),
        },
    },
)
async def create_study(payload: StudyCreate, db: DbSession) -> Study:
    return await service.create_study(db, payload)


@router.get(
    "",
    response_model=StudyListResponse,
    status_code=status.HTTP_200_OK,
    summary="List the studies (history)",
    description=(
        "Studies newest first, filtered by product sheet and/or status, paginated. This is "
        "what a history sidebar is built from."
    ),
    responses={status.HTTP_200_OK: {"description": "One page of the study history"}},
)
async def list_studies(
    db: DbSession,
    product_id: ProductFilter = None,
    study_status: StatusFilter = None,
    limit: ListLimit = DEFAULT_LIST_LIMIT,
    offset: ListOffset = 0,
) -> StudyListResponse:
    items, total = await service.list_studies(
        db, product_id=product_id, status=study_status, limit=limit, offset=offset
    )
    return StudyListResponse(
        items=[StudyResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{study_id}",
    response_model=StudyResponse,
    status_code=status.HTTP_200_OK,
    summary="State of a study",
    description=(
        "Current status of the study, its per-module `progress`, and its `error` if it "
        "failed. This is the endpoint a client polls while the pipeline runs."
    ),
    responses={
        status.HTTP_200_OK: {"description": "The study"},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No study with this identifier",
        },
    },
)
async def get_study(study: ExistingStudy) -> Study:
    return study


@router.get(
    "/{study_id}/sources",
    response_model=StudySourceListResponse,
    status_code=status.HTTP_200_OK,
    summary="Collection results of a study (one entry per collector)",
    description=(
        "How each of the six collectors went for this study: its `status`, its `error` if "
        "it failed, its exit code and its duration. A collector appears here as soon as it "
        "has finished, so the list fills up while the study is still collecting.\n\n"
        "`status` is `skipped_region` when the collector does not cover the region of the "
        "study (Amazon has no Moroccan site, for instance): that is a normal outcome, not "
        "a failure.\n\n"
        "The collected JSON itself is **not** included -- a single payload can weigh "
        "several megabytes. Read one with `GET /studies/{study_id}/sources/{source}`."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "The collector rows written so far, ordered by source name"
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No study with this identifier",
        },
    },
)
async def list_study_sources(study: ExistingStudy, db: DbSession) -> StudySourceListResponse:
    items = await service.list_study_sources(db, study.id)
    return StudySourceListResponse(
        items=[StudySourceSummary.model_validate(item) for item in items],
        total=len(items),
    )


@router.get(
    "/{study_id}/sources/{source}",
    response_model=StudySourceResponse,
    status_code=status.HTTP_200_OK,
    summary="Raw data collected by one collector",
    description=(
        "The JSON this collector printed, exactly as the pipeline produced it, plus the "
        "same execution metadata as the listing.\n\n"
        "`payload` is null whenever `status` is not `succeeded`: a collector that failed or "
        "that does not cover the region wrote no data, and `error` says why.\n\n"
        "A 404 here means the collector has not written a row yet -- the study is still "
        "collecting, or never reached that stage -- which is a different thing from a "
        "collector that ran and produced nothing."
    ),
    responses={
        status.HTTP_200_OK: {"description": "The collector row, payload included"},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No study with this identifier, or no data for this collector yet",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unknown collector name",
        },
    },
)
async def get_study_source(
    study: ExistingStudy, source: SourceName, db: DbSession
) -> StudySourceData:
    return await service.get_study_source(db, study.id, source)


@router.get(
    "/{study_id}/report",
    response_model=StudyReportResponse,
    status_code=status.HTTP_200_OK,
    summary="The report produced by a study",
    description=(
        "The deliverable: the full report in Markdown, its executive summary, and F7's "
        "own JSON output (market, product, hypotheses, analysis statuses, coherence "
        "alerts, stated limits).\n\n"
        "`rapport_markdown` runs to tens of thousands of characters -- it is the report "
        "itself, not an excerpt.\n\n"
        "A 404 means F7 has not produced a report yet: the study is still running, or it "
        "failed. A study that reached `completed` or `partial` always has one, since a "
        "failed report is precisely what makes a study `failed`.\n\n"
        "**A negative verdict is a result, not a failure**: such a study is `completed` "
        "and its report says so."
    ),
    responses={
        status.HTTP_200_OK: {"description": "The report and its executive summary"},
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No study with this identifier, or this study has no report yet",
        },
    },
)
async def get_study_report(study: ExistingStudy, db: DbSession) -> StudyReport:
    return await service.get_study_report(db, study.id)
