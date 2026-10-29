from pathlib import Path
from stat import S_IMODE

import pytest
from pydantic import SecretStr, ValidationError

from app.database.config import DatabaseSettings
from app.database.local_setup import create_configuration

URL = "postgresql+psycopg://cgl_migrator:private-password@127.0.0.1:55432/cgl_dev"


def test_database_credentials_are_masked() -> None:
    settings = DatabaseSettings(
        app_env="test", migration_database_url=SecretStr(URL), _env_file=None
    )
    assert "private-password" not in repr(settings)
    assert settings.migration_database_url.get_secret_value() == URL


@pytest.mark.parametrize(
    "value",
    ["not-a-url-with-private-password", "sqlite:///private-password", "postgresql://u:p@h/d"],
)
def test_invalid_database_url_is_rejected_without_echoing_it(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        DatabaseSettings(app_env="test", migration_database_url=SecretStr(value), _env_file=None)
    assert value not in str(caught.value)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_migrations_remain_local_until_deployment_controls_exist(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CGL_APP_ENV", environment)
    with pytest.raises(ValidationError):
        DatabaseSettings(migration_database_url=SecretStr(URL), _env_file=None)


def test_generated_credentials_are_private_and_do_not_overwrite_existing_files(
    tmp_path: Path,
) -> None:
    destination = tmp_path / ".env.database"
    assert create_configuration(destination)
    original = destination.read_bytes()
    assert S_IMODE(destination.stat().st_mode) == 0o600
    assert not create_configuration(destination, port=55433)
    assert destination.read_bytes() == original
    settings = DatabaseSettings(_env_file=destination)
    assert settings.migration_database_url.get_secret_value() != URL
    assert settings.test_admin_database_url is not None
    assert settings.test_admin_database_url != settings.migration_database_url


def test_invalid_local_port_does_not_create_config(tmp_path: Path) -> None:
    destination = tmp_path / ".env.database"
    with pytest.raises(ValueError, match="Port"):
        create_configuration(destination, port=0)
    assert not destination.exists()
