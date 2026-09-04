"""Exceptions of the ``products`` domain."""

from fastapi import status

from src.exceptions import BadGateway, DetailedHTTPException, NotFound, UnprocessableContent
from src.products.constants import ErrorCode


class ProductNotFound(NotFound):
    DETAIL = ErrorCode.PRODUCT_NOT_FOUND


class UnsupportedImageType(UnprocessableContent):
    DETAIL = ErrorCode.IMAGE_TYPE_NOT_ALLOWED


class ImageTooLarge(DetailedHTTPException):
    STATUS_CODE = status.HTTP_413_CONTENT_TOO_LARGE
    DETAIL = ErrorCode.IMAGE_TOO_LARGE


class ImageUploadFailed(BadGateway):
    DETAIL = ErrorCode.IMAGE_UPLOAD_FAILED


# ---------------------------------------------------------------------------
# Automated extraction -- the HTTP face of the agent's failures.
# The translation happens in `extraction.py`; the router never sees an agent exception.
# ---------------------------------------------------------------------------
class UnsupportedProductUrl(UnprocessableContent):
    DETAIL = ErrorCode.UNSUPPORTED_URL


class PlatformNotExtractable(UnprocessableContent):
    """The URL is a genuine product page on a site no scraper can currently read.

    422 rather than 502: nothing failed, and nothing will succeed on a retry. The
    distinction matters to whoever reads the response — a 502 invites a retry that
    would cost two actor runs to reach the same place.
    """

    DETAIL = ErrorCode.PLATFORM_NOT_EXTRACTABLE


class ExtractionIncomplete(UnprocessableContent):
    DETAIL = ErrorCode.EXTRACTION_INCOMPLETE


class ProductPageLoadFailed(BadGateway):
    DETAIL = ErrorCode.PAGE_LOAD_FAILED


class ScraperRunFailed(BadGateway):
    DETAIL = ErrorCode.ACTOR_RUN_FAILED


class ExtractionNotConfigured(DetailedHTTPException):
    STATUS_CODE = status.HTTP_500_INTERNAL_SERVER_ERROR
    DETAIL = ErrorCode.EXTRACTION_NOT_CONFIGURED


class ExtractionTimedOut(DetailedHTTPException):
    STATUS_CODE = status.HTTP_504_GATEWAY_TIMEOUT
    DETAIL = ErrorCode.EXTRACTION_TIMEOUT


class ExtractionFailed(DetailedHTTPException):
    STATUS_CODE = status.HTTP_500_INTERNAL_SERVER_ERROR
    DETAIL = ErrorCode.EXTRACTION_FAILED
