"""Exceptions of the ``studies`` domain."""

import uuid

from fastapi import HTTPException, status

from src.exceptions import DetailedHTTPException, NotFound, UnprocessableContent
from src.studies.constants import ErrorCode


class StudyNotFound(NotFound):
    DETAIL = ErrorCode.STUDY_NOT_FOUND


class StudySourceNotFound(NotFound):
    DETAIL = ErrorCode.STUDY_SOURCE_NOT_FOUND


class StudyReportNotFound(NotFound):
    DETAIL = ErrorCode.STUDY_REPORT_NOT_FOUND


class StudyProductNotFound(NotFound):
    DETAIL = ErrorCode.PRODUCT_NOT_FOUND


class RegionRequired(UnprocessableContent):
    DETAIL = ErrorCode.REGION_REQUIRED


class StudyAlreadyRunning(DetailedHTTPException):
    """409 carrying the identifier of the study that holds the lock.

    The body is ``{"detail": {"message": ..., "study_id": ...}}``: the caller needs the
    running study to poll it, and inventing a second study for the same (product, region)
    would burn ~2 $ of LLM credits and 30-60 min of Apify quota for nothing.
    """

    STATUS_CODE = status.HTTP_409_CONFLICT
    DETAIL = ErrorCode.STUDY_ALREADY_RUNNING

    def __init__(self, study_id: uuid.UUID) -> None:
        HTTPException.__init__(
            self,
            status_code=self.STATUS_CODE,
            detail={"message": str(self.DETAIL), "study_id": str(study_id)},
        )


class StudyReportNotReplayable(HTTPException):
    """409 -- the report cannot be replayed because an analysis is missing.

    A replay reads the persisted F3 to F5 payloads and re-runs F7 alone. Without one of
    them there is nothing to write up, and re-running the whole study is a different
    request with a different cost -- so this is a refusal, not a silent fallback.
    """

    def __init__(self, manquantes: list[str]) -> None:
        """Build the refusal, naming what is missing.

        Args:
            manquantes: Identifiers of the analyses that are absent.
        """
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The report cannot be replayed: "
                + ", ".join(manquantes)
                + " missing from this study. Only the write-up is replayed; the "
                "analyses themselves are not re-run."
            ),
        )
