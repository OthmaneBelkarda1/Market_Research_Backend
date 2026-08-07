"""Async engine, session factory and request-scoped session dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped ``AsyncSession``.

    The session is closed (and any open transaction rolled back) when the request ends,
    so a failure raised downstream never leaves a half-written row behind.
    """
    async with SessionFactory() as session:
        yield session
