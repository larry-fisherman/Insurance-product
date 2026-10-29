"""Two restricted pools: identity metadata and tenant-scoped business transactions."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        if settings.runtime_database_url is None or settings.auth_database_url is None:
            raise ValueError("Workspace database configuration is required")
        self.runtime_engine = sa.create_engine(
            settings.runtime_database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            hide_parameters=True,
        )
        self.auth_engine = sa.create_engine(
            settings.auth_database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=3,
            hide_parameters=True,
        )

    @contextmanager
    def identity(self) -> Iterator[Session]:
        with Session(self.auth_engine, expire_on_commit=False) as session, session.begin():
            yield session

    @contextmanager
    def tenant(self, tenant_id: UUID, user_id: UUID) -> Iterator[Session]:
        with Session(self.runtime_engine, expire_on_commit=False) as session, session.begin():
            session.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            session.execute(
                sa.text("SELECT set_config('app.user_id', :user, true)"), {"user": str(user_id)}
            )
            yield session

    def close(self) -> None:
        self.runtime_engine.dispose()
        self.auth_engine.dispose()
