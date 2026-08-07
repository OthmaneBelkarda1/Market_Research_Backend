"""create the study tables

Additive only: four new tables, no existing table is touched. ``study.product_id`` is a
foreign key to ``product.id``; the three result tables cascade on the study they belong to,
because a study's collected data has no meaning without the study.

The result payloads are ``jsonb`` on purpose: their shapes are JSON contracts owned and
versioned by the pipeline modules, not a relational model this database re-declares.

Revision ID: 20260806_0004
Revises: 20260725_0003
Create Date: 2026-08-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260806_0004"
down_revision: str | None = "20260725_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STUDY_STATUSES = (
    "created",
    "collecting",
    "analyzing",
    "reporting",
    "completed",
    "partial",
    "failed",
)
_TRIGGER_SOURCES = ("products", "extractions", "manual")
_SOURCES = ("google_trends", "reddit", "recherche_web", "aliexpress", "amazon", "meta_ads")
_SOURCE_STATUSES = ("succeeded", "failed", "skipped_region")
_AGENTS = ("f3_insights", "f4_concurrence", "f5_verdict", "f6_plc")
_ANALYSIS_STATUSES = ("succeeded", "failed")


def _in_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "study",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("langue", sa.Text(), nullable=True),
        sa.Column("devise", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="created", nullable=False),
        sa.Column("trigger_source", sa.Text(), nullable=False),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_check("status", _STUDY_STATUSES), name="status"),
        sa.CheckConstraint(_in_check("trigger_source", _TRIGGER_SOURCES), name="trigger_source"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="study_product_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="study_pkey"),
    )
    op.create_index("study_product_id_idx", "study", ["product_id"], unique=False)

    op.create_table(
        "study_source_data",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_check("source", _SOURCES), name="source"),
        sa.CheckConstraint(_in_check("status", _SOURCE_STATUSES), name="status"),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["study.id"],
            name="study_source_data_study_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="study_source_data_pkey"),
        sa.UniqueConstraint("study_id", "source", name="study_source_data_study_id_key"),
    )
    op.create_index(
        "study_source_data_study_id_idx", "study_source_data", ["study_id"], unique=False
    )

    op.create_table(
        "study_analysis",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_check("agent", _AGENTS), name="agent"),
        sa.CheckConstraint(_in_check("status", _ANALYSIS_STATUSES), name="status"),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["study.id"],
            name="study_analysis_study_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="study_analysis_pkey"),
        sa.UniqueConstraint("study_id", "agent", name="study_analysis_study_id_key"),
    )
    op.create_index("study_analysis_study_id_idx", "study_analysis", ["study_id"], unique=False)

    op.create_table(
        "study_report",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rapport_markdown", sa.Text(), nullable=False),
        sa.Column("resume_markdown", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["study.id"],
            name="study_report_study_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="study_report_pkey"),
        sa.UniqueConstraint("study_id", name="study_report_study_id_key"),
    )


def downgrade() -> None:
    op.drop_table("study_report")
    op.drop_index("study_analysis_study_id_idx", table_name="study_analysis")
    op.drop_table("study_analysis")
    op.drop_index("study_source_data_study_id_idx", table_name="study_source_data")
    op.drop_table("study_source_data")
    op.drop_index("study_product_id_idx", table_name="study")
    op.drop_table("study")
