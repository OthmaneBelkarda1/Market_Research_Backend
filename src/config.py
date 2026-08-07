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


settings = Config()
