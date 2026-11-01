"""Validated startup configuration; no configuration is read at import time."""

from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CGL_",
        env_file=[
            Path(__file__).resolve().parents[1] / ".env",
            Path(__file__).resolve().parents[1] / ".env.app",
        ],
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Environment
    runtime_database_url: SecretStr | None = None
    auth_database_url: SecretStr | None = None
    app_secret: SecretStr | None = None
    redis_url: SecretStr | None = None
    public_origin: str = "http://localhost:3000"
    synthetic_only: bool = True
    storage_backend: Literal["s3", "filesystem"] = "filesystem"
    storage_path: Path = Path(".data/objects")
    s3_endpoint: str | None = None
    s3_bucket: str = "cgl-private"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    parser_endpoint: str | None = None
    extraction_provider: Literal["local", "gateway", "openai"] = "local"
    extraction_endpoint: str | None = None
    extraction_key: SecretStr | None = None
    extraction_model: str = ""
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_required_amr: str = "mfa"
    auth_mode: Literal["local", "oidc"] = "local"
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_jwks_uri: str | None = None
    marketing_origin: str = "http://127.0.0.1:3001"
    session_idle_minutes: int = 30

    @property
    def session_cookie(self) -> str:
        return "__Host-app_session" if self.public_origin.startswith("https://") else "cgl_session"

    @property
    def csrf_cookie(self) -> str:
        return "__Host-app_csrf" if self.public_origin.startswith("https://") else "cgl_csrf"

    @model_validator(mode="after")
    def require_supported_environment(self) -> "Settings":
        for origin in (self.public_origin, self.marketing_origin):
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Configure plain HTTP(S) public origins")
        if self.parser_endpoint:
            parser_url = urlsplit(self.parser_endpoint)
            if (
                parser_url.scheme not in {"http", "https"}
                or not parser_url.hostname
                or parser_url.username
                or parser_url.password
                or parser_url.query
                or parser_url.fragment
                or parser_url.path not in {"", "/"}
            ):
                raise ValueError("Parser endpoint must be a fixed HTTP(S) origin")
        if not 5 <= self.session_idle_minutes <= 60:
            raise ValueError("Session idle timeout must be 5–60 minutes")
        if self.auth_mode == "oidc":
            if not all(
                (
                    self.oidc_issuer,
                    self.oidc_client_id,
                    self.oidc_client_secret,
                    self.oidc_authorization_endpoint,
                    self.oidc_token_endpoint,
                    self.oidc_jwks_uri,
                )
            ):
                raise ValueError(
                    "Managed identity requires an explicit issuer, client and endpoints"
                )
            for endpoint in (
                self.oidc_issuer,
                self.oidc_authorization_endpoint,
                self.oidc_token_endpoint,
                self.oidc_jwks_uri,
            ):
                parsed = urlsplit(endpoint or "")
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.fragment
                    or parsed.query
                ):
                    raise ValueError("OIDC endpoints must be approved HTTPS URLs")
        if self.app_env not in (Environment.LOCAL, Environment.TEST):
            raise ValueError(
                "This foundation supports only local and test environments. "
                "Staging/production require the planned identity and tenancy controls."
            )
        if not self.synthetic_only:
            raise ValueError(
                "Real-data use requires the documented vendor, legal, and security gates"
            )
        if self.runtime_database_url or self.auth_database_url:
            if not (self.runtime_database_url and self.auth_database_url and self.app_secret):
                raise ValueError("Workspace requires runtime/auth database URLs and an app secret")
            if len(self.app_secret.get_secret_value()) < 32:
                raise ValueError("App secret must contain at least 32 characters")
        if self.extraction_provider == "openai" and not (
            self.extraction_key and self.extraction_model
        ):
            raise ValueError("OpenAI extraction requires an explicit model and API key")
        if self.extraction_provider == "gateway" and not self.extraction_endpoint:
            raise ValueError("Gateway extraction requires an approved HTTPS endpoint")
        return self
