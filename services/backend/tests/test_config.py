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
