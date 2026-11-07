"""Application factory and process-level health contracts."""

import logging
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

import redis
import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)
from starlette.responses import JSONResponse, Response

from app.config import Settings
from app.database.revision import EXPECTED_ALEMBIC_HEAD
from app.database.runtime import Database
from app.limiting import Limiter
from app.logging_setup import REQUEST_ID, configure_logging, inbound_request_id
from app.storage import StorageCapacityError

APP_VERSION = version("cgl-api")

log = logging.getLogger("cgl.api")

# SQLSTATEs with a stable, non-transient meaning. psycopg classes 42501 as a
# ProgrammingError and 55000 as an OperationalError, so they are checked before type.
INSUFFICIENT_PRIVILEGE = "42501"
OBJECT_NOT_IN_PREREQUISITE_STATE = "55000"

# Every readiness dependency probe is bounded by this many seconds.
READINESS_TIMEOUT_SECONDS = 2


def route_template(request: Request) -> str:
    """The matched route path template; never the raw path or query string."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def request_id_of(request: Request) -> str:
    current = getattr(request.state, "request_id", None) or REQUEST_ID.get()
    return str(current or "")


def error_fields(request: Request, code: str, error: BaseException) -> dict[str, Any]:
    return {
        "request_id": request_id_of(request) or None,
        "route": route_template(request),
        "method": request.method,
        "code": code,
        "exception_type": type(error).__name__,
        "tenant_id": getattr(request.state, "log_tenant_id", None),
    }


def internal_error(request: Request) -> JSONResponse:
    request_id = request_id_of(request)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "INTERNAL_ERROR",
                "message": "The request could not be completed. Quote the request ID to support.",
                "request_id": request_id,
            }
        },
        headers={
            "X-Request-ID": request_id,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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


def bounded(probe: Callable[[], bool]) -> bool:
    """Run one probe with the readiness timeout. A hung probe is abandoned, not awaited."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readiness")
    try:
        return pool.submit(probe).result(timeout=READINESS_TIMEOUT_SECONDS)
    except TimeoutError:
        return False
    finally:
        pool.shutdown(wait=False)


def database_ready(database: Database) -> bool:
    """Identity pool answers and the schema is at the code's expected Alembic head.

    Uses the identity (cgl_auth) pool, granted SELECT on public.alembic_version only (0014).
    """
    try:
        with database.identity() as session:
            session.execute(
                sa.text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{READINESS_TIMEOUT_SECONDS}s"},
            )
            heads = session.scalars(sa.text("SELECT version_num FROM public.alembic_version"))
            return list(heads) == [EXPECTED_ALEMBIC_HEAD]
    except SQLAlchemyError:
        return False


def redis_ready(limiter: Limiter) -> bool:
    # Isolated tests use bounded local counters. A configured workspace must name Redis
    # (Settings validator); the limiter client carries 2 s socket timeouts.
    if limiter.client is None:
        return True
    try:
        return bool(limiter.client.ping())
    except redis.RedisError:
        return False


def storage_writable(root: Path) -> bool:
    """Create and remove one private probe file in the existing filesystem storage root."""
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".readiness-") as probe:
            probe.write(b"0")
            probe.flush()
        return True
    except OSError:
        return False


def dependencies_ready(app: FastAPI) -> bool:
    """Database and schema head, Redis, and storage. The response never names the failing one."""
    configuration: Settings = app.state.settings
    ready = (
        bounded(lambda: database_ready(app.state.database))
        and redis_ready(app.state.limiter)
        and (
            # Only the filesystem backend is probed; S3 writes stay disabled.
            configuration.storage_backend != "filesystem"
            or bounded(lambda: storage_writable(configuration.storage_path))
        )
    )
    if not ready:
        log.warning("readiness_dependency_unavailable", extra={"code": "NOT_READY"})
    return ready


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else Settings()
    configure_logging()
    app = FastAPI(
        title="CGL Workspace API",
        version=APP_VERSION,
        description="Tenant-isolated CGL submission review and export. Synthetic local/test use only.",
        lifespan=lifespan,
        # Swagger and the served schema are diagnostic surfaces with their own switch.
        # app.openapi() still builds the contract when they are not served.
        docs_url="/api/v1/docs" if configuration.api_docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json" if configuration.api_docs_enabled else None,
    )
    app.state.settings = configuration
    app.state.ready = False
    app.state.database = Database(configuration) if configuration.runtime_database_url else None
    app.state.limiter = Limiter(configuration)

    @app.exception_handler(StorageCapacityError)
    async def storage_capacity_error(request: Request, error: StorageCapacityError) -> JSONResponse:
        return JSONResponse(
            status_code=503 if error.code == "STORAGE_QUOTA_UNAVAILABLE" else 507,
            content={
                "detail": {
                    "code": error.code,
                    "message": "Private storage cannot accept more files. Contact your administrator before retrying.",
                }
            },
        )

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
        sqlstate = getattr(error.orig, "sqlstate", None) if isinstance(error, DBAPIError) else None
        if sqlstate == INSUFFICIENT_PRIVILEGE:
            log.error(
                "database_access_policy_violation",
                extra=error_fields(request, "ACCESS_POLICY_VIOLATION", error),
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": {
                        "code": "ACCESS_POLICY_VIOLATION",
                        "message": "The request was refused by a data access policy.",
                        "request_id": request_id_of(request),
                    }
                },
            )
        if sqlstate == OBJECT_NOT_IN_PREREQUISITE_STATE:
            log.warning(
                "database_immutable_record", extra=error_fields(request, "IMMUTABLE_RECORD", error)
            )
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "IMMUTABLE_RECORD",
                        "message": "This record is part of preserved history and cannot be changed.",
                    }
                },
            )
        if isinstance(error, (OperationalError, InterfaceError)):
            log.warning(
                "database_unavailable", extra=error_fields(request, "DATABASE_UNAVAILABLE", error)
            )
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "The workspace is temporarily unavailable. Please retry.",
                    }
                },
            )
        log.error("database_error", extra=error_fields(request, "INTERNAL_ERROR", error))
        return internal_error(request)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, error: Exception) -> JSONResponse:
        # Catch-all: type and route only. Never the message, body, headers or traceback.
        # Starlette re-raises after this handler; the server's own error line goes through
        # the same content-free formatter (ADR 0010).
        log.error("unhandled_exception", extra=error_fields(request, "INTERNAL_ERROR", error))
        return internal_error(request)

    @app.middleware("http")
    async def response_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = inbound_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        token = REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status = 500  # An exception escaping here is answered by the catch-all handler.
        try:
            response = await bounded_dispatch(request, call_next)
            status = response.status_code
            response.headers["X-Request-ID"] = str(request_id)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "route": route_template(request),
                    "method": request.method,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "tenant_id": getattr(request.state, "log_tenant_id", None),
                },
            )
            REQUEST_ID.reset(token)

    async def bounded_dispatch(
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
        return await call_next(request)

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
            dependency_ready = dependencies_ready(request.app)
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
    from app.oidc import router as oidc_router
    from app.orphan_cleanup import router as orphan_cleanup_router
    from app.platform import router as platform_router
    from app.upload_cleanup import router as upload_cleanup_router
    from app.uploads import router as uploads_router

    app.include_router(oidc_router)
    app.include_router(platform_router)
    app.include_router(uploads_router)
    app.include_router(upload_cleanup_router)
    app.include_router(orphan_cleanup_router)
    app.include_router(identity_router)
    app.include_router(workflow_router)
    app.include_router(demo_router)
    return app
