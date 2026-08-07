"""add product.source_url

Additive only: ``source_url`` records the product page an extraction was made from
(``ProductSummary.source_url``). It is nullable because every row created by F1
(``POST /products``) has no source URL, and F1 must keep working unchanged.

Revision ID: 20260725_0003
Revises: 20260724_0002
Create Date: 2026-07-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260725_0003"
down_revision: str | None = "20260724_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product", sa.Column("source_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("product", "source_url")
