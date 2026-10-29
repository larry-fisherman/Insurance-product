"""Migration-only credentials, isolated from application settings and responses."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CGL_",
        env_file=Path(__file__).resolve().parents[2] / ".env.database",
        extra="ignore",  # File also supplies PostgreSQL bootstrap variables to Compose.
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test"]
    migration_database_url: SecretStr
    test_admin_database_url: SecretStr | None = None

    @field_validator("migration_database_url", "test_admin_database_url")
    @classmethod
    def require_postgresql(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        try:
            parsed = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("Database URL must be a valid PostgreSQL psycopg URL") from None
        if parsed.drivername != "postgresql+psycopg" or not parsed.database or not parsed.username:
            raise ValueError("Database URL requires postgresql+psycopg, a user, and a database")
        return value
