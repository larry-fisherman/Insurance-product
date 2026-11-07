import json
from typing import Any
from unittest.mock import MagicMock, Mock
from uuid import UUID, uuid4

import psycopg
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Environment, Settings
from app.main import APP_VERSION, create_app


def test_liveness_does_not_depend_on_startup() -> None:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "cgl-api", "version": APP_VERSION}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_readiness_tracks_application_lifespan() -> None:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    client = TestClient(app)
    assert client.get("/api/v1/health/ready").status_code == 503
    with client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "service": "cgl-api",
            "version": APP_VERSION,
            "scope": "application",
        }
    assert client.get("/api/v1/health/ready").status_code == 503


def test_openapi_documents_failure_and_success_contracts() -> None:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    responses = app.openapi()["paths"]["/api/v1/health/ready"]["get"]["responses"]
    assert set(responses) == {"200", "503"}
    for status in responses.values():
        assert status["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ReadinessResponse"
        )


def test_readiness_does_not_reopen_dependencies_outside_lifespan() -> None:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    dependency = Mock()
    dependency.identity.side_effect = AssertionError("Inactive application must not open a session")
    app.state.database = dependency
    client = TestClient(app)
    assert client.get("/api/v1/health/ready").status_code == 503
    with client:
        app.state.ready = False
        assert client.get("/api/v1/health/ready").status_code == 503
    assert client.get("/api/v1/health/ready").status_code == 503
    dependency.identity.assert_not_called()
    dependency.close.assert_called_once()


def test_product_routes_require_authentication() -> None:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    with TestClient(app) as client:
        assert client.post("/api/v1/submissions", json={}).status_code == 401


def json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def failing_app(error: Exception) -> FastAPI:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))

    @app.get("/api/v1/test-failure/{item}")
    def failure(item: str) -> None:
        raise error

    return app


def test_supplied_request_id_is_echoed_and_invalid_one_is_replaced() -> None:
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    supplied = str(uuid4())
    with TestClient(app) as client:
        echoed = client.get("/api/v1/health/live", headers={"X-Request-ID": supplied})
        replaced = client.get("/api/v1/health/live", headers={"X-Request-ID": "not-a-uuid\n"})
    assert echoed.headers["x-request-id"] == supplied
    assert replaced.headers["x-request-id"] != "not-a-uuid\n"
    UUID(replaced.headers["x-request-id"])


def test_access_log_line_is_structured_and_content_free(capsys: pytest.CaptureFixture[str]) -> None:
    sentinel = "SENTINEL-" + uuid4().hex
    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    supplied = str(uuid4())
    with TestClient(app) as client:
        client.cookies.set("unrelated", sentinel)
        response = client.post(
            f"/api/v1/submissions?note={sentinel}",
            json={"insured": sentinel},
            headers={"X-Request-ID": supplied, "X-Note": sentinel},
        )
    assert response.status_code == 401
    output = capsys.readouterr().out
    assert sentinel not in output
    access = [line for line in json_lines(output) if line.get("event") == "request"]
    assert access[-1]["request_id"] == supplied
    assert access[-1]["route"] == "/api/v1/submissions"
    assert access[-1]["method"] == "POST"
    assert access[-1]["status"] == 401
    assert isinstance(access[-1]["duration_ms"], float)


@pytest.mark.parametrize(
    ("sqlstate_error", "status", "code"),
    [
        (psycopg.errors.ObjectNotInPrerequisiteState, 409, "IMMUTABLE_RECORD"),
        (psycopg.errors.InsufficientPrivilege, 500, "ACCESS_POLICY_VIOLATION"),
        (psycopg.errors.UndefinedColumn, 500, "INTERNAL_ERROR"),
    ],
)
def test_database_errors_map_by_sqlstate(
    sqlstate_error: type[psycopg.Error],
    status: int,
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "SENTINEL-" + uuid4().hex
    wrapped = sa.exc.DBAPIError.instance(
        "UPDATE app.x", None, sqlstate_error(sentinel), psycopg.Error
    )
    with TestClient(failing_app(wrapped)) as client:
        response = client.get("/api/v1/test-failure/1")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    output = capsys.readouterr().out
    assert sentinel not in output
    if status == 500:
        assert response.json()["detail"]["request_id"] == response.headers["x-request-id"]
        logged = [line for line in json_lines(output) if line.get("code") == code]
        assert logged and logged[0]["route"] == "/api/v1/test-failure/{item}"


def test_connection_errors_remain_retryable_503() -> None:
    wrapped = sa.exc.OperationalError("SELECT 1", None, psycopg.OperationalError("down"))
    with TestClient(failing_app(wrapped)) as client:
        response = client.get("/api/v1/test-failure/1")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"


def test_unhandled_exception_logs_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    sentinel = "SENTINEL-" + uuid4().hex
    with TestClient(failing_app(ValueError(sentinel)), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/test-failure/" + sentinel)
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "INTERNAL_ERROR"
    assert detail["request_id"] == response.headers["x-request-id"]
    output = capsys.readouterr().out
    assert sentinel not in output
    logged = [line for line in json_lines(output) if line.get("event") == "unhandled_exception"]
    assert logged[0]["exception_type"] == "ValueError"
    assert logged[0]["request_id"] == detail["request_id"]
    assert logged[0]["route"] == "/api/v1/test-failure/{item}"
    access = [line for line in json_lines(output) if line.get("event") == "request"]
    assert access[-1]["status"] == 500
    assert access[-1]["request_id"] == detail["request_id"]


def test_api_docs_switch_hides_served_schema_but_keeps_contract_export() -> None:
    enabled = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    disabled = create_app(
        Settings(app_env=Environment.TEST, api_docs_enabled=False, _env_file=None)
    )
    with TestClient(enabled) as client:
        assert client.get("/api/v1/docs").status_code == 200
        assert client.get("/api/v1/openapi.json").status_code == 200
    with TestClient(disabled) as client:
        assert client.get("/api/v1/docs").status_code == 404
        assert client.get("/api/v1/openapi.json").status_code == 404
    assert disabled.openapi() == enabled.openapi()


def test_demo_switch_returns_not_found_without_touching_dependencies() -> None:
    app = create_app(Settings(app_env=Environment.TEST, demo_enabled=False, _env_file=None))
    limiter = Mock()
    app.state.limiter = limiter
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/demo", json={}, headers={"Origin": "http://testserver"}
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DEMO_DISABLED"
    limiter.allow.assert_not_called()


def test_demo_global_cap_applies_in_addition_to_the_address_key() -> None:
    from app.demo import DEMO_GLOBAL_KEY, DEMO_GLOBAL_PER_HOUR

    app = create_app(Settings(app_env=Environment.TEST, _env_file=None))
    seen: list[tuple[str, int, int]] = []

    def allow(key: str, maximum: int, seconds: int) -> bool:
        seen.append((key, maximum, seconds))
        return key != DEMO_GLOBAL_KEY

    app.state.limiter = Mock(allow=allow)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/demo", json={}, headers={"Origin": "http://testserver"}
        )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "RATE_LIMITED"
    assert seen[0][0] != DEMO_GLOBAL_KEY and seen[0][1:] == (5, 60)
    assert seen[1] == (DEMO_GLOBAL_KEY, DEMO_GLOBAL_PER_HOUR, 3600)


def test_expected_alembic_head_matches_the_migration_scripts() -> None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.database.revision import EXPECTED_ALEMBIC_HEAD

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [EXPECTED_ALEMBIC_HEAD]


def workspace_app(tmp_path: Any, head: str, ping: Any = True) -> FastAPI:
    from app.database.revision import EXPECTED_ALEMBIC_HEAD

    app = create_app(
        Settings(
            app_env=Environment.TEST,
            storage_backend="filesystem",
            storage_path=tmp_path,
            _env_file=None,
        )
    )
    session = MagicMock()
    session.scalars.return_value = iter([EXPECTED_ALEMBIC_HEAD if head == "expected" else head])
    database = MagicMock()
    database.identity.return_value.__enter__.return_value = session
    app.state.database = database
    client = Mock()
    if isinstance(ping, BaseException):
        client.ping.side_effect = ping
    else:
        client.ping.return_value = ping
    app.state.limiter.client = client
    return app


def test_readiness_checks_schema_head_redis_and_storage(tmp_path: Any) -> None:
    app = workspace_app(tmp_path, "expected")
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    app.state.limiter.client.ping.assert_called_once()
    assert list(tmp_path.iterdir()) == []  # The storage probe leaves nothing behind.


@pytest.mark.parametrize("failure", ["head", "redis", "storage"])
def test_readiness_fails_closed_without_naming_the_dependency(tmp_path: Any, failure: str) -> None:
    import redis

    storage = tmp_path / "missing" if failure == "storage" else tmp_path
    app = workspace_app(
        storage,
        "0013_exception_accepted_at" if failure == "head" else "expected",
        ping=redis.ConnectionError("unavailable") if failure == "redis" else True,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "cgl-api",
        "version": APP_VERSION,
        "scope": "application",
    }
