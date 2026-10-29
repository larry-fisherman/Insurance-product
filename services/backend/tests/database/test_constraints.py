from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database.models import (
    AppUser,
    AuditEvent,
    AuthSession,
    Invitation,
    Membership,
    MembershipRole,
    OutboxEvent,
    Role,
    Tenant,
)

pytestmark = pytest.mark.database


@dataclass(frozen=True)
class Actors:
    tenant_a: UUID
    tenant_b: UUID
    user_a: UUID
    user_b: UUID
    member_a: UUID
    member_b: UUID


@pytest.fixture
def actors(connection: Connection) -> Actors:
    identities = Actors(*(uuid4() for _ in range(6)))
    connection.execute(
        sa.insert(Tenant),
        [
            {"id": identities.tenant_a, "name": "Fictional Agency A"},
            {"id": identities.tenant_b, "name": "Fictional Agency B"},
        ],
    )
    connection.execute(
        sa.insert(AppUser),
        [
            {
                "id": identities.user_a,
                "identity_issuer": "https://identity.example.test",
                "identity_subject": "fictional-a",
                "display_name": "Fictional User A",
            },
            {
                "id": identities.user_b,
                "identity_issuer": "https://identity.example.test",
                "identity_subject": "fictional-b",
                "display_name": "Fictional User B",
            },
        ],
    )
    connection.execute(
        sa.insert(Membership),
        [
            {
                "id": identities.member_a,
                "tenant_id": identities.tenant_a,
                "user_id": identities.user_a,
            },
            {
                "id": identities.member_b,
                "tenant_id": identities.tenant_b,
                "user_id": identities.user_b,
            },
        ],
    )
    return identities


def expect_integrity(connection: Connection, statement: Any, sqlstate: str) -> None:
    with pytest.raises(IntegrityError) as caught, connection.begin_nested():
        connection.execute(statement)
    assert getattr(caught.value.orig, "sqlstate", None) == sqlstate


def audit_values(actors: Actors) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": actors.tenant_a,
        "actor_type": "member",
        "actor_membership_id": actors.member_a,
        "action": "tenant.reviewed",
        "object_type": "tenant",
        "object_id": actors.tenant_a,
        "request_id": uuid4(),
    }


def invitation_values(actors: Actors) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": actors.tenant_a,
        "email": "fictional@example.test",
        "token_hash": uuid4().hex * 2,
        "inviter_membership_id": actors.member_a,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }


def outbox_values(actors: Actors) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": actors.tenant_a,
        "event_type": "tenant.reviewed",
        "aggregate_type": "tenant",
        "aggregate_id": actors.tenant_a,
        "payload": {"tenant_id": str(actors.tenant_a)},
    }


def test_global_identity_uniqueness_includes_issuer(connection: Connection, actors: Actors) -> None:
    del actors
    values = {
        "identity_issuer": "https://identity.example.test",
        "identity_subject": "fictional-a",
        "display_name": "Duplicate",
    }
    expect_integrity(connection, sa.insert(AppUser).values(**values), "23505")
    connection.execute(sa.insert(AppUser).values(**{**values, "identity_issuer": "other-issuer"}))


def test_one_membership_per_user_and_agency(connection: Connection, actors: Actors) -> None:
    expect_integrity(
        connection,
        sa.insert(Membership).values(tenant_id=actors.tenant_a, user_id=actors.user_a),
        "23505",
    )
    connection.execute(
        sa.insert(Membership).values(tenant_id=actors.tenant_b, user_id=actors.user_a)
    )


@pytest.mark.parametrize("wrong_target", ["membership", "grantor"])
def test_role_assignment_rejects_cross_tenant_links(
    connection: Connection, actors: Actors, wrong_target: str
) -> None:
    role_id = connection.execute(sa.select(Role.id).where(Role.key == "viewer")).scalar_one()
    expect_integrity(
        connection,
        sa.insert(MembershipRole).values(
            tenant_id=actors.tenant_a,
            membership_id=actors.member_b if wrong_target == "membership" else actors.member_a,
            granted_by_membership_id=actors.member_b
            if wrong_target == "grantor"
            else actors.member_a,
            role_id=role_id,
        ),
        "23503",
    )


def test_invitation_inviter_must_belong_to_same_tenant(
    connection: Connection, actors: Actors
) -> None:
    values = {**invitation_values(actors), "inviter_membership_id": actors.member_b}
    expect_integrity(connection, sa.insert(Invitation).values(**values), "23503")


def test_invitation_acceptor_must_belong_to_same_tenant(
    connection: Connection, actors: Actors
) -> None:
    values = {
        **invitation_values(actors),
        "status": "accepted",
        "accepted_membership_id": actors.member_b,
        "accepted_at": datetime.now(UTC),
    }
    expect_integrity(connection, sa.insert(Invitation).values(**values), "23503")


def test_only_one_pending_invitation_per_tenant_email(
    connection: Connection, actors: Actors
) -> None:
    values = invitation_values(actors)
    connection.execute(sa.insert(Invitation).values(**values))
    expect_integrity(connection, sa.insert(Invitation).values(**invitation_values(actors)), "23505")
    connection.execute(
        sa.update(Invitation).where(Invitation.id == values["id"]).values(status="revoked")
    )
    connection.execute(sa.insert(Invitation).values(**invitation_values(actors)))


@pytest.mark.parametrize(
    "change",
    [
        {"token_hash": "plaintext-token"},
        {"email": "UNNORMALIZED@example.test"},
        {"status": "accepted"},
        {"expires_at": datetime(2000, 1, 1, tzinfo=UTC)},
    ],
)
def test_invitation_requires_safe_hash_and_valid_state(
    connection: Connection, actors: Actors, change: dict[str, Any]
) -> None:
    expect_integrity(
        connection, sa.insert(Invitation).values(**{**invitation_values(actors), **change}), "23514"
    )


def test_session_hash_uniqueness_and_expiry(connection: Connection, actors: Actors) -> None:
    values = {
        "user_id": actors.user_a,
        "secret_hash": "a" * 64,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    connection.execute(sa.insert(AuthSession).values(**values))
    expect_integrity(connection, sa.insert(AuthSession).values(**values), "23505")
    expect_integrity(
        connection,
        sa.insert(AuthSession).values(
            **{**values, "secret_hash": "b" * 64, "expires_at": datetime(2000, 1, 1, tzinfo=UTC)}
        ),
        "23514",
    )


def test_audit_actor_cannot_reference_another_tenant(
    connection: Connection, actors: Actors
) -> None:
    expect_integrity(
        connection,
        sa.insert(AuditEvent).values(
            **{**audit_values(actors), "actor_membership_id": actors.member_b}
        ),
        "23503",
    )


def test_audit_requires_attributed_actor(connection: Connection, actors: Actors) -> None:
    expect_integrity(
        connection,
        sa.insert(AuditEvent).values(**{**audit_values(actors), "actor_membership_id": None}),
        "23514",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE app.audit_event SET action = 'overwritten'",
        "DELETE FROM app.audit_event",
        "TRUNCATE app.audit_event",
    ],
)
def test_audit_history_cannot_be_rewritten_or_truncated(
    connection: Connection, actors: Actors, statement: str
) -> None:
    connection.execute(sa.insert(AuditEvent).values(**audit_values(actors)))
    with pytest.raises(DBAPIError) as caught, connection.begin_nested():
        connection.exec_driver_sql(statement)
    assert getattr(caught.value.orig, "sqlstate", None) == "55000"
    assert connection.execute(sa.select(sa.func.count()).select_from(AuditEvent)).scalar_one() == 1


def test_disabling_user_keeps_history_and_deletion_cannot_cascade(
    connection: Connection, actors: Actors
) -> None:
    values = audit_values(actors)
    connection.execute(sa.insert(AuditEvent).values(**values))
    connection.execute(
        sa.update(AppUser).where(AppUser.id == actors.user_a).values(status="disabled")
    )
    expect_integrity(
        connection, sa.delete(Membership).where(Membership.id == actors.member_a), "23503"
    )
    assert (
        connection.execute(
            sa.select(AuditEvent.actor_membership_id).where(AuditEvent.id == values["id"])
        ).scalar_one()
        == actors.member_a
    )


def test_outbox_rolls_back_with_business_write(connection: Connection) -> None:
    tenant_id, event_id = uuid4(), uuid4()

    class AbortUnitOfWork(Exception):
        pass

    with pytest.raises(AbortUnitOfWork), connection.begin_nested():
        connection.execute(sa.insert(Tenant).values(id=tenant_id, name="Rollback agency"))
        connection.execute(
            sa.insert(OutboxEvent).values(
                id=event_id,
                tenant_id=tenant_id,
                event_type="tenant.created",
                aggregate_type="tenant",
                aggregate_id=tenant_id,
                payload={"tenant_id": str(tenant_id)},
            )
        )
        raise AbortUnitOfWork
    assert (
        connection.execute(sa.select(Tenant.id).where(Tenant.id == tenant_id)).scalar_one_or_none()
        is None
    )
    assert (
        connection.execute(
            sa.select(OutboxEvent.id).where(OutboxEvent.id == event_id)
        ).scalar_one_or_none()
        is None
    )


def test_outbox_deduplication_is_tenant_scoped(connection: Connection, actors: Actors) -> None:
    values = {**outbox_values(actors), "dedupe_key": "example-key"}
    connection.execute(sa.insert(OutboxEvent).values(**values))
    expect_integrity(
        connection, sa.insert(OutboxEvent).values(**{**values, "id": uuid4()}), "23505"
    )
    connection.execute(
        sa.insert(OutboxEvent).values(**{**values, "id": uuid4(), "tenant_id": actors.tenant_b})
    )


@pytest.mark.parametrize("change", [{"attempts": -1}, {"event_version": 0}, {"payload": []}])
def test_outbox_rejects_invalid_state(
    connection: Connection, actors: Actors, change: dict[str, Any]
) -> None:
    expect_integrity(
        connection, sa.insert(OutboxEvent).values(**{**outbox_values(actors), **change}), "23514"
    )


def test_outbox_can_mark_publication_without_deleting_event(
    connection: Connection, actors: Actors
) -> None:
    values = outbox_values(actors)
    connection.execute(sa.insert(OutboxEvent).values(**values))
    connection.execute(
        sa.update(OutboxEvent)
        .where(OutboxEvent.id == values["id"])
        .values(attempts=1, published_at=sa.func.clock_timestamp())
    )
    assert (
        connection.execute(
            sa.select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None))
        ).scalar_one_or_none()
        is None
    )
    assert connection.execute(sa.select(sa.func.count()).select_from(OutboxEvent)).scalar_one() == 1


def test_uuid_and_utc_defaults_are_database_compatible(
    connection: Connection, actors: Actors
) -> None:
    row = connection.execute(
        sa.select(Tenant.id, Tenant.created_at, Tenant.row_version).where(
            Tenant.id == actors.tenant_a
        )
    ).one()
    assert isinstance(row.id, UUID)
    assert row.created_at.utcoffset() is not None
    assert row.row_version == 1
