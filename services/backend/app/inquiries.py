"""Isolated public inquiry process. Never reads operations configuration or credentials."""

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from app.common import canonical_json, digest, now


class InquirySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INQUIRY_", hide_input_in_errors=True)
    database_url: str = "sqlite:///.data/inquiries/contacts.sqlite3"
    environment: Literal["local", "test"] = "local"


class InquiryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    purpose: Literal["demo", "pricing", "contact"]
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    agency: str = Field(min_length=2, max_length=160)
    team_size: Literal["", "1–5", "6–20", "21–100", "100+"] = ""
    message: str = Field(default="", max_length=1500)
    website: str = Field(default="", max_length=200)
    consent: Literal[True]

    @field_validator("email")
    @classmethod
    def email_valid(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Valid email required")
        return value.lower()


metadata = sa.MetaData()
inquiries = sa.Table(
    "inquiry",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("request_key", sa.String(100), unique=True, nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
)


def engine_for(configuration: InquirySettings) -> sa.Engine:
    url = sa.engine.make_url(configuration.database_url)
    if url.drivername not in {"sqlite", "postgresql+psycopg"}:
        raise ValueError("Unsupported inquiry database")
    return sa.create_engine(url, hide_parameters=True)


def create_app(configuration: InquirySettings | None = None) -> FastAPI:
    configuration = configuration or InquirySettings()
    app = FastAPI(title="Public inquiry intake", docs_url=None, redoc_url=None, openapi_url=None)
    engine = engine_for(configuration)

    @app.get("/health")
    def health() -> dict[str, bool]:
        with engine.connect() as connection:
            connection.execute(sa.select(inquiries.c.id).limit(1))
        return {"ready": True}

    @app.post("/inquiries")
    async def receive(request: Request) -> JSONResponse:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"received": False}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                return JSONResponse({"received": False}, status_code=413)
        try:
            payload = InquiryInput.model_validate_json(body)
            key = str(UUID(request.headers.get("idempotency-key", "")))
        except (ValidationError, ValueError):
            return JSONResponse({"received": False}, status_code=422)
        if payload.website:
            return JSONResponse({"received": True}, status_code=201)
        fingerprint = digest(canonical_json(payload.model_dump()))
        try:
            with engine.begin() as connection:
                if engine.dialect.name == "postgresql":
                    connection.execute(sa.text("SELECT pg_advisory_xact_lock(749208123)"))
                else:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                prior = connection.execute(
                    sa.select(inquiries.c.request_hash).where(inquiries.c.request_key == key)
                ).scalar_one_or_none()
                if prior is not None:
                    return JSONResponse(
                        {"received": prior == fingerprint},
                        status_code=200 if prior == fingerprint else 409,
                    )
                # Serialize the aggregate bound across replicas and process restarts.
                count = (
                    connection.scalar(
                        sa.select(sa.func.count())
                        .select_from(inquiries)
                        .where(inquiries.c.created_at >= now() - timedelta(minutes=1))
                    )
                    or 0
                )
                if count >= 30:
                    return JSONResponse({"received": False}, status_code=429)
                connection.execute(
                    inquiries.insert().values(
                        id=str(uuid4()),
                        created_at=now(),
                        request_key=key,
                        request_hash=fingerprint,
                        payload=payload.model_dump(exclude={"website"}),
                    )
                )
        except SQLAlchemyError:
            return JSONResponse({"received": False}, status_code=503)
        return JSONResponse(
            {"received": True}, status_code=201, headers={"Cache-Control": "no-store"}
        )

    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Isolated inquiry database maintenance")
    parser.add_argument("action", choices=["migrate", "count"])
    args = parser.parse_args()
    configuration = InquirySettings()
    url = sa.engine.make_url(configuration.database_url)
    if url.drivername == "sqlite" and url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = engine_for(configuration)
    if args.action == "migrate":
        metadata.create_all(engine)
        if url.drivername == "sqlite" and url.database:
            Path(url.database).chmod(0o600)
        print("Isolated inquiry schema ready.")
    else:
        with engine.connect() as connection:
            print(
                json.dumps(
                    {
                        "inquiries": connection.scalar(
                            sa.select(sa.func.count()).select_from(inquiries)
                        )
                    }
                )
            )


if __name__ == "__main__":
    main()
