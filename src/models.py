"""Shared ORM and Pydantic bases."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, field_serializer
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

POSTGRES_INDEXES_NAMING_CONVENTION = {
    "ix": "%(column_0_label)s_idx",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

metadata = MetaData(naming_convention=POSTGRES_INDEXES_NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base shared by every domain ORM model."""

    metadata = metadata


class CustomModel(BaseModel):
    """Base Pydantic model with a project-wide datetime serialization."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    @field_serializer("*", mode="wrap", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value: Any, handler: SerializerFunctionWrapHandler) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo("UTC"))
            return value.strftime("%Y-%m-%dT%H:%M:%S%z")
        return handler(value)


class ErrorResponse(CustomModel):
    """Shape of every error body returned by the API (FastAPI's ``HTTPException``)."""

    detail: str
