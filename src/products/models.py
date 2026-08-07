"""ORM model of the ``product`` table."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base


class Product(Base):
    __tablename__ = "product"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    # Country the market study must cover, as an ISO 3166-1 alpha-2 code. Kept as free
    # ``Text`` (no CHECK) because rows created before the studies domain existed hold
    # display names such as "Ile-de-France"; the API validates every new value, and
    # ``studies.service.resolve_product_region`` refuses to guess a country from an old one.
    region: Mapped[str] = mapped_column(Text, nullable=False)
    # URL of the product image: either supplied as-is at creation, or the public Supabase
    # Storage URL produced by an upload. The binary itself never lives in the database.
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Product page an extraction was made from (``extractions`` domain). Always null for
    # sheets created by hand through F1, hence nullable.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
