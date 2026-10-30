"""Application factory and process-level health contracts."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Literal
from uuid import uuid4

import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import JSONResponse, Response

from app.config import Settings
from app.database.runtime import Database
from app.limiting import Limiter

APP_VERSION = version("cgl-api")


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    service: Literal["cgl-api"] = "cgl-api"
    version: str = APP_VERSION


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "not_ready"]
    service: Literal["cgl-api"] = "cgl-api"
    version: str = APP_VERSION
    scope: Literal["application"] = "application"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        if app.state.database:
            app.state.database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    app = FastAPI(
        title="CGL Workspace API",
        version=APP_VERSION,
        description="Tenant-isolated CGL submission review and export. Synthetic local/test use only.",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = configuration
    app.state.ready = False
    app.state.database = Database(configuration) if configuration.runtime_database_url else None
    app.state.limiter = Limiter(configuration)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "INVALID_REQUEST",
                    "message": "Check the submitted fields and their formats.",
                    "fields": [".".join(str(v) for v in item["loc"]) for item in error.errors()],
                }
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, error: IntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "WRITE_CONFLICT",
                    "message": "This request conflicts with an existing record. Refresh before trying again.",
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "The workspace is temporarily unavailable. Please retry.",
                }
            },
        )

    @app.middleware("http")
    async def response_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse(
                    status_code=411,
                    content={
                        "detail": {
                            "code": "LENGTH_REQUIRED",
                            "message": "Supply a bounded request body.",
                        }
                    },
                )
            maximum = 51 * 1024 * 1024 if request.url.path.endswith("/documents") else 1024 * 1024
            if int(length) > maximum:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": {
                            "code": "BODY_TOO_LARGE",
                            "message": "The request exceeds the permitted size.",
                        }
                    },
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(uuid4())
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    router = APIRouter(prefix="/api/v1/health", tags=["health"])

    @router.get("/live", response_model=LivenessResponse, operation_id="getLiveness")
    def live() -> LivenessResponse:
        """Process is responding. Does not check databases or other future dependencies."""
        return LivenessResponse()

    @router.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse, "description": "Application not started"}},
        operation_id="getReadiness",
    )
    def ready(request: Request) -> ReadinessResponse | JSONResponse:
        """Application lifespan started. Dependency checks will be added as implemented."""
        dependency_ready = True
        if request.app.state.ready and request.app.state.database:
            try:
                with request.app.state.database.identity() as session:
                    session.execute(sa.text("SELECT 1"))
            except SQLAlchemyError:
                dependency_ready = False
        if not request.app.state.ready or not dependency_ready:
            return JSONResponse(
                status_code=503,
                content=ReadinessResponse(status="not_ready").model_dump(),
            )
        return ReadinessResponse(status="ready")

    app.include_router(router)
    from app.api import router as workflow_router
    from app.demo import router as demo_router
    from app.identity import router as identity_router

    app.include_router(identity_router)
    app.include_router(workflow_router)
    app.include_router(demo_router)
    return app
