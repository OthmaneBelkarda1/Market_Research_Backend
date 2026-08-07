"""replace product.source_url with product.region

Revision ID: 20260724_0002
Revises: 20260722_0001
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260724_0002"
down_revision: str | None = "20260722_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only used to backfill rows created before ``region`` existed; the default is dropped
# right after, so every new insert must supply the region explicitly.
_BACKFILL_REGION = "France"


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column("region", sa.Text(), nullable=False, server_default=_BACKFILL_REGION),
    )
    op.alter_column("product", "region", server_default=None)
    op.drop_column("product", "source_url")


def downgrade() -> None:
    op.add_column("product", sa.Column("source_url", sa.Text(), nullable=True))
    op.drop_column("product", "region")
