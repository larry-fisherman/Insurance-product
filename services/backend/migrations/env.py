"""Run migrations explicitly, never as an API startup side effect."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from app import platform_models  # noqa: F401
from app.database.config import DatabaseSettings
from app.database.models import Base
from app.domain import models as domain_models  # noqa: F401

config = context.config


def include_name(name: str | None, type_: str, _parents: object) -> bool:
    # Only compare our managed schema; never propose deleting public/extension tables.
    if type_ == "schema":
        return name == "app"
    return name != "alembic_version" if type_ == "table" else True


def migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema="public",
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="public",
    )
    with context.begin_transaction():
        context.run_migrations()
elif (provided_connection := config.attributes.get("connection")) is not None:
    migrate(provided_connection)
else:
    settings = DatabaseSettings()
    engine = create_engine(
        settings.migration_database_url.get_secret_value(),
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
