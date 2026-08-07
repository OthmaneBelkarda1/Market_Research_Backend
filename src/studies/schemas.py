"""Pydantic schemas of the ``studies`` domain."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from src.models import CustomModel
from src.studies.config import studies_settings
from src.studies.constants import (
    LANGUE_PATTERN,
    REGION_PATTERN,
    StudySource,
    StudySourceStatus,
    StudyStatus,
    StudyTrigger,
)

# ISO 3166-1 alpha-2, uppercase. The whitelist is configuration, so membership is enforced
# by a validator rather than by a Literal type.
StudyRegion = Annotated[str, StringConstraints(pattern=REGION_PATTERN)]
# ISO 639-1, lowercase. Optional: F8.2 derives it from the region when it is absent.
StudyLangue = Annotated[str, StringConstraints(pattern=LANGUE_PATTERN)]


def normalize_region(value: object) -> object:
    """Uppercase a region code, then check it against the configured whitelist.

    Runs *before* the pattern constraint so ``ma`` is accepted and stored as ``MA``; a
    value that is not two letters falls through to the pattern check and its 422.
    """
    if not isinstance(value, str):
        return value
    region = value.strip().upper()
    looks_like_a_code = len(region) == 2 and region.isalpha()
    if looks_like_a_code and region not in studies_settings.allowed_regions:
        raise ValueError(
            f"Region {region!r} is not allowed. "
            f"Accepted values: {', '.join(studies_settings.sorted_allowed_regions)}."
        )
    return region


class StudyCreate(CustomModel):
    """Manual creation (or relaunch) of a study for an existing product sheet."""

    product_id: uuid.UUID = Field(description="Identifier of the product sheet to study.")
    region: StudyRegion | None = Field(
        default=None,
        description=(
            "Market the study covers (ISO 3166-1 alpha-2, from the configured whitelist). "
            "Falls back to `product.region` when omitted; a study with no region at all is "
            "refused with 422 -- a region is never inferred."
        ),
    )
    langue: StudyLangue | None = Field(
        default=None,
        description=(
            "Language of the study (ISO 639-1). Derived from the region at run start when "
            "omitted. One study is always one language."
        ),
    )

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> object:
        return normalize_region(value)

    @field_validator("langue", mode="before")
    @classmethod
    def _normalize_langue(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"product_id": "c8b8bbea-1649-4356-903d-2bcfea0cecda", "region": "MA"}]
        }
    )


class StudyResponse(CustomModel):
    """A study as stored, whatever its stage."""

    id: uuid.UUID
    product_id: uuid.UUID
    region: str
    langue: str | None = None
    devise: str | None = None
    status: StudyStatus
    trigger_source: StudyTrigger
    progress: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-module progress, filled while the pipeline runs (F8.2).",
    )
    error: dict[str, Any] | None = Field(
        default=None, description="`{code, message}` when the study failed."
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class StudyListResponse(CustomModel):
    """One page of the study history."""

    items: list[StudyResponse]
    total: int = Field(description="Total number of studies matching the filters.")
    limit: int
    offset: int


class StudySourceSummary(CustomModel):
    """How one collector went, without what it collected.

    The payload is deliberately absent from this shape rather than nulled out: a null
    payload already means "this collector produced nothing", and a listing must not be
    read as that. It is also what keeps the listing cheap -- a single collector payload
    can weigh several megabytes.
    """

    id: uuid.UUID
    study_id: uuid.UUID
    source: StudySource
    status: StudySourceStatus = Field(
        description=(
            "`skipped_region` is a normal outcome, not a failure: the collector does not "
            "cover the region of the study."
        )
    )
    error: str | None = Field(
        default=None, description="Tail of the module's stderr when it failed."
    )
    exit_code: int | None = None
    duration_seconds: float | None = None
    created_at: datetime
    updated_at: datetime


class StudySourceResponse(StudySourceSummary):
    """One collector run, with the JSON it produced."""

    payload: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw JSON of the collector, as the pipeline owns and versions it. Always null "
            "when `status` is not `succeeded`."
        ),
    )


class StudySourceListResponse(CustomModel):
    """Every collector this study has a row for -- at most one per source."""

    items: list[StudySourceSummary]
    total: int = Field(description="Number of collectors that have written a row.")


# Two keys of F7's output that name files inside the study's workdir. Dropped from the
# response, not from the database: an API client already receives their content in
# `rapport_markdown` and `resume_markdown`, so all they add is a path that means nothing
# on the caller's side and invites reading a file that is not theirs to read. The row
# keeps them, because the workdir files are what settle a doubt about the report.
REPORT_WORKDIR_KEYS = frozenset({"chemin_rapport", "chemin_resume"})


class StudyReportResponse(CustomModel):
    """The deliverable of a study: the report F7 produced, and its executive summary."""

    id: uuid.UUID
    study_id: uuid.UUID
    rapport_markdown: str = Field(
        description=(
            "The full report, in Markdown, exactly as F7 wrote it. Counts tens of "
            "thousands of characters: this is the deliverable, not a preview."
        )
    )
    resume_markdown: str | None = Field(
        default=None,
        description="The executive summary, when F7 produced one alongside the report.",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description=(
            "F7's own JSON output: market, product, hypotheses, analysis statuses, "
            "coherence alerts and stated limits. The contract is the pipeline's, not "
            "this API's -- minus the two workdir file names, which say nothing to a "
            "caller that already holds the two Markdown texts."
        ),
    )
    created_at: datetime

    @field_validator("payload")
    @classmethod
    def _drop_workdir_paths(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """Shallow, and only these two keys.

        `sources_utilisees[].fichier` names an upstream JSON on purpose -- that is the
        report's provenance, which a reader needs to trace a figure back to its collector.
        """
        if value is None:
            return None
        return {key: item for key, item in value.items() if key not in REPORT_WORKDIR_KEYS}


class StudyConflictDetail(CustomModel):
    """Body of the 409: the caller gets the running study back, to poll it."""

    message: str
    study_id: uuid.UUID


class StudyConflictResponse(CustomModel):
    detail: StudyConflictDetail
