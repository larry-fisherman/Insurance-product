import pytest
from pydantic import ValidationError

from app.config import Environment, Settings
from app.main import create_app


def test_missing_environment_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CGL_APP_ENV", raising=False)
    with pytest.raises(ValidationError, match="app_env"):
        Settings(_env_file=None)


def test_environment_is_read_from_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CGL_APP_ENV", "local")
    settings = Settings(_env_file=None)
    assert settings.app_env is Environment.LOCAL


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_unsafe_deployment_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("CGL_APP_ENV", environment)
    with pytest.raises(ValidationError, match="identity and tenancy controls"):
        Settings(_env_file=None)


def test_invalid_environment_error_does_not_echo_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CGL_APP_ENV", "unexpected-sensitive-input")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert "unexpected-sensitive-input" not in str(caught.value)


def test_application_factory_validates_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CGL_APP_ENV", "production")
    with pytest.raises(ValidationError):
        create_app()


@pytest.mark.parametrize(
    "app,marketing",
    [
        ("http://localhost:3000", "http://localhost:3001"),
        ("https://APP.example.invalid", "http://app.example.invalid:8080"),
        ("https://app.example.invalid.", "https://app.example.invalid"),
        ("http://[::1]:3000", "http://[::1]:3001"),
    ],
)
def test_same_cookie_hostname_fails_closed(app: str, marketing: str) -> None:
    with pytest.raises(ValidationError, match="different cookie hostnames"):
        Settings(
            _env_file=None, app_env=Environment.TEST, public_origin=app, marketing_origin=marketing
        )


def test_distinct_https_hosts_use_host_only_secure_cookie_names() -> None:
    configuration = Settings(
        _env_file=None,
        app_env=Environment.TEST,
        public_origin="https://app.example.invalid",
        marketing_origin="https://www.example.invalid",
    )
    assert configuration.session_cookie == "__Host-app_session"
    assert configuration.csrf_cookie == "__Host-app_csrf"
