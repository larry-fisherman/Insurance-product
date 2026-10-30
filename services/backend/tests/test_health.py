from unittest.mock import Mock

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
