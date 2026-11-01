"""Explicit local operator actions; never imported by an HTTP application."""

import argparse
import os
import secrets
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.common import digest, now
from app.database.config import DatabaseSettings
from app.database.models import AuditEvent, Tenant
from app.platform_models import AgencyActivation, Subscription


def main() -> None:
    parser = argparse.ArgumentParser(description="Local synthetic agency administration")
    commands = parser.add_subparsers(dest="command", required=True)
    activation = commands.add_parser("activation")
    activation.add_argument("--email", required=True)
    activation.add_argument("--agency", required=True)
    activation.add_argument(
        "--reference", required=True, help="Operator/change reference retained in history"
    )
    activation.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private file for the one-time token; never stdout",
    )
    subscription = commands.add_parser("subscription")
    subscription.add_argument("--tenant", type=UUID, required=True)
    subscription.add_argument(
        "--status", choices=["manual_pilot", "active", "restricted", "cancelled"], required=True
    )
    subscription.add_argument("--seats", type=int, required=True)
    subscription.add_argument("--submissions", type=int, required=True)
    subscription.add_argument("--reference", required=True)
    args = parser.parse_args()
    settings = DatabaseSettings()
    url = sa.engine.make_url(settings.migration_database_url.get_secret_value())
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "cgl_dev":
        parser.error("This operator command is restricted to the loopback cgl_dev database")
    if len(args.reference.strip()) < 5:
        parser.error("Supply an attributable operator/change reference")
    engine = sa.create_engine(url, hide_parameters=True)
    try:
        with Session(engine) as session, session.begin():
            if args.command == "activation":
                if "@" not in args.email or not 2 <= len(args.agency) <= 160:
                    parser.error("Supply a valid fictional email and agency name")
                token = secrets.token_urlsafe(32)
                # Reserve the output before the transaction; never overwrite a private token.
                descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(token + "\n")
                session.add(
                    AgencyActivation(
                        token_hash=digest(token),
                        email=args.email.lower(),
                        agency_name=args.agency,
                        expires_at=now() + timedelta(days=7),
                        operator_reference=args.reference,
                    )
                )
            else:
                if not 1 <= args.seats <= 10000 or not 1 <= args.submissions <= 1000000:
                    parser.error("Choose positive bounded limits")
                tenant = session.get(Tenant, args.tenant, with_for_update=True)
                row = session.scalar(
                    sa.select(Subscription)
                    .where(Subscription.tenant_id == args.tenant)
                    .with_for_update()
                )
                if tenant is None or row is None:
                    parser.error("Agency not found")
                before = {
                    "status": row.status,
                    "seat_limit": row.seat_limit,
                    "submission_limit": row.submission_limit,
                }
                row.status, row.seat_limit, row.submission_limit = (
                    args.status,
                    args.seats,
                    args.submissions,
                )
                row.row_version += 1
                session.add(
                    AuditEvent(
                        tenant_id=args.tenant,
                        actor_type="system",
                        action="billing.operator_updated",
                        object_type="agency_subscription",
                        object_id=row.id,
                        request_id=uuid4(),
                        change_summary={
                            "operator_reference": args.reference,
                            "before": before,
                            "after": {
                                "status": row.status,
                                "seat_limit": row.seat_limit,
                                "submission_limit": row.submission_limit,
                            },
                        },
                    )
                )
    finally:
        engine.dispose()
    print("Local agency administration completed. No messages or payments were sent.")


if __name__ == "__main__":
    main()
