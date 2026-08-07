"""ORM models of the ``studies`` domain: the study and the three tables it fills.

The three result tables are deliberately generic (``payload`` in ``jsonb``) rather than one
relational model per analysis axis: the output shapes are JSON contracts owned and
versioned by the pipeline, whose agents re-declare in Pydantic (``extra="ignore"``) the
only fields they consume. The database is the blackboard the modules exchange through, not
a second declaration of those contracts.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.studies.constants import (
    StudyAgent,
    StudyAnalysisStatus,
    StudySource,
    StudySourceStatus,
    StudyStatus,
    StudyTrigger,
)


def _values_check(column: str, values: type[StrEnum]) -> str:
    """SQL fragment restricting ``column`` to the members of an enumeration."""
    listed = ", ".join(f"'{member.value}'" for member in values)
    return f"{column} IN ({listed})"


class Study(Base):
    __tablename__ = "study"
    __table_args__ = (
        CheckConstraint(_values_check("status", StudyStatus), name="status"),
        CheckConstraint(_values_check("trigger_source", StudyTrigger), name="trigger_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product.id"),
        nullable=False,
        index=True,
    )
    # ISO 3166-1 alpha-2, uppercase. Never inferred: it comes from the request or from the
    # product sheet, and it drives every collector (proxy country, marketplace site).
    region: Mapped[str] = mapped_column(Text, nullable=False)
    # ISO 639-1. Null until the run starts: F8.2 derives it from the region through the
    # pipeline's own module. One study is always one language, never an average of two.
    langue: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Filled at run start (F8.2) from the region, for the AliExpress collector.
    devise: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=StudyStatus.CREATED.value
    )
    trigger_source: Mapped[str] = mapped_column(Text, nullable=False)
    # Per-module progress, written as the run advances (F8.2):
    # {"aliexpress": {"status": "running", "started_at": ...}, ...}
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # {"code": ..., "message": ...} -- never a secret, never a raw credential.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class StudySourceData(Base):
    """One row per collector: the JSON it printed on stdout, or why it did not."""

    __tablename__ = "study_source_data"
    __table_args__ = (
        CheckConstraint(_values_check("source", StudySource), name="source"),
        CheckConstraint(_values_check("status", StudySourceStatus), name="status"),
        UniqueConstraint("study_id", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("study.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Tail of the module's stderr when it failed. Truncated by the runner (F8.2).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class StudyAnalysis(Base):
    """One row per analysis agent (F3 to F6). Same shape as ``study_source_data``."""

    __tablename__ = "study_analysis"
    __table_args__ = (
        CheckConstraint(_values_check("agent", StudyAgent), name="agent"),
        CheckConstraint(_values_check("status", StudyAnalysisStatus), name="status"),
        UniqueConstraint("study_id", "agent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("study.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class StudyReport(Base):
    """The deliverable produced by F7: at most one report per study."""

    __tablename__ = "study_report"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("study.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rapport_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    resume_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Raw F7 output when it carries more than the two texts above.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
