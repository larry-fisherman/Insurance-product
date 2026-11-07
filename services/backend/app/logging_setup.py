"""Content-free JSON-line operational logging and request correlation (ADR 0010).

Records carry an allowlisted set of structured fields only. Message arguments are
interpolated solely for this application's own ``cgl.*`` loggers, whose call sites pass
identifiers, counts and codes; third-party loggers emit their static template without
arguments. Exception messages and tracebacks are never written: only the type name.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

REQUEST_ID: ContextVar[UUID | None] = ContextVar("cgl_request_id", default=None)

FIELDS = (
    "request_id",
    "route",
    "method",
    "status",
    "duration_ms",
    "tenant_id",
    "code",
    "exception_type",
    "job_id",
    "attempt",
)

_HANDLER_MARKER = "_cgl_json_handler"


def inbound_request_id(value: str | None) -> UUID:
    """Accept only a canonical UUID from the client; otherwise generate a new one."""
    if value and len(value) <= 64:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if str(parsed) == value.lower():
                return parsed
    return uuid4()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.name == "cgl" or record.name.startswith("cgl."):
            event = record.getMessage()
        else:
            event = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
        line: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
        }
        for field in FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                line[field] = value if isinstance(value, (int, float)) else str(value)
        if "request_id" not in line and (current := REQUEST_ID.get()) is not None:
            line["request_id"] = str(current)
        if record.exc_info and record.exc_info[0] is not None and "exception_type" not in line:
            line["exception_type"] = record.exc_info[0].__name__
        return json.dumps(line, separators=(",", ":"), ensure_ascii=True)


def configure_logging(level: int = logging.INFO) -> None:
    """Install one stdout JSON handler on the root and uvicorn loggers. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    for name in ("", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [h for h in logger.handlers if not getattr(h, _HANDLER_MARKER, False)]
        if name:
            # Replace uvicorn's plain-text handlers so its lines are also JSON and content-free.
            logger.handlers = []
            logger.propagate = True
        else:
            logger.addHandler(handler)
            if logger.level == logging.NOTSET or logger.level > level:
                logger.setLevel(level)
