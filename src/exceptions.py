"""Global exception bases.

Domains subclass ``DetailedHTTPException`` in their own ``exceptions.py`` so that a route
never has to build an ``HTTPException`` by hand.
"""

from typing import Any

from fastapi import HTTPException, status


class DetailedHTTPException(HTTPException):
    STATUS_CODE = status.HTTP_500_INTERNAL_SERVER_ERROR
    DETAIL = "Server error."

    def __init__(self, detail: str | None = None, **kwargs: Any) -> None:
        super().__init__(status_code=self.STATUS_CODE, detail=detail or self.DETAIL, **kwargs)


class NotFound(DetailedHTTPException):
    STATUS_CODE = status.HTTP_404_NOT_FOUND
    DETAIL = "Resource not found."


class BadGateway(DetailedHTTPException):
    STATUS_CODE = status.HTTP_502_BAD_GATEWAY
    DETAIL = "Upstream service error."


class UnprocessableContent(DetailedHTTPException):
    STATUS_CODE = status.HTTP_422_UNPROCESSABLE_CONTENT
    DETAIL = "Unprocessable content."
