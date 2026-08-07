"""Global application settings.

Domain-scoped settings live in ``src/{domain}/config.py`` (see ``src/products/config.py``).
"""

from enum import StrEnum

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: Environment = Environment.LOCAL

    # Supabase PostgreSQL connection string (Session Pooler), adapted to the asyncpg driver:
    # postgresql+asyncpg://postgres.<ref>:<password>@<host>:5432/postgres
    DATABASE_URL: PostgresDsn

    # Origins the browser front-end is served from, comma-separated. A browser refuses a
    # cross-origin response that does not name its origin back, so without this the API is
    # unreachable from any web client -- which is exactly what a missing CORS header looks
    # like from the front end.
    #
    # Comma-separated rather than a JSON list, for the same reason as
    # STUDY_ALLOWED_REGIONS: pydantic-settings parses a `list[str]` field as JSON, which
    # would reject the natural `CORS_ORIGINS=https://a.app,https://b.app`.
    #
    # Empty by default, and no `*` fallback: an origin allowed by accident is an origin
    # any site may call the API from, and this API has no authentication to fall back on.
    CORS_ORIGINS: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """The allowed origins, normalized. Empty entries are ignored."""
        return [part.strip() for part in self.CORS_ORIGINS.split(",") if part.strip()]


settings = Config()
