"""Small shared contracts and safe operational helpers."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, NoReturn
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.models import AuditEvent


def now() -> datetime:
    return datetime.now(UTC)


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def json_value(value: Any) -> Any:
    if isinstance(value, (UUID, date, datetime, Decimal)):
        return str(value) if isinstance(value, (UUID, Decimal)) else value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record(value: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    hidden = exclude or set()
    return {
        column.key: json_value(getattr(value, column.key))
        for column in sa.inspect(type(value)).column_attrs
        if column.key not in hidden
    }


def fail(status: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def audit(
    session: Session,
    tenant_id: UUID,
    membership_id: UUID,
    action: str,
    object_type: str,
    object_id: UUID | None = None,
    summary: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_type="member",
            actor_membership_id=membership_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            request_id=uuid4(),
            change_summary=summary or {},
        )
    )
