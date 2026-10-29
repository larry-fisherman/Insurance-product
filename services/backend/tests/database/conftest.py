"""Disposable databases only: never downgrade, truncate, or drop the developer database."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.pool import NullPool

from app.database.config import DatabaseSettings

BACKEND = Path(__file__).resolve().parents[2]
DatabaseFactory = Callable[[], AbstractContextManager[Engine]]


@pytest.fixture(scope="session")
def migration_config() -> Callable[[Connection], Config]:
    def configure(connection: Connection) -> Config:
        config = Config(str(BACKEND / "alembic.ini"))
        config.attributes["connection"] = connection
        return config

    return configure


@pytest.fixture(scope="session")
def database_factory() -> Iterator[DatabaseFactory]:
    settings = DatabaseSettings()
    if settings.test_admin_database_url is None:
        pytest.fail("Database tests require local configuration: run make db-config and make db-up")
    admin_url = make_url(settings.test_admin_database_url.get_secret_value())
    migration_url = make_url(settings.migration_database_url.get_secret_value())
    if (
        admin_url.host not in {"127.0.0.1", "localhost"}
        or migration_url.host != admin_url.host
        or migration_url.port != admin_url.port
        or admin_url.database != "cgl_dev"
        or migration_url.database != "cgl_dev"
        or admin_url.username != "cgl_admin"
        or migration_url.username != "cgl_migrator"
    ):
        pytest.fail("Database tests require the loopback-only cgl_dev development configuration")

    admin_engine = sa.create_engine(
        admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT", hide_parameters=True
    )

    @contextmanager
    def create_database() -> Iterator[Engine]:
        database_name = "cgl_test_" + uuid4().hex
        quoted = admin_engine.dialect.identifier_preparer.quote(database_name)
        # The generated name is unique. Never reuse an existing database or clean cgl_dev.
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE {quoted} OWNER cgl_migrator TEMPLATE template0"
            )
        engine = sa.create_engine(
            migration_url.set(database=database_name), poolclass=NullPool, hide_parameters=True
        )
        try:
            yield engine
        finally:
            engine.dispose()
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")

    try:
        yield create_database
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def database_engine(
    database_factory: DatabaseFactory, migration_config: Callable[[Connection], Config]
) -> Iterator[Engine]:
    with database_factory() as engine:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine


@pytest.fixture
def connection(database_engine: Engine) -> Iterator[Connection]:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
