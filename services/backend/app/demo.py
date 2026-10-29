"""Isolated fictional review data; no shared passwords or customer documents."""

import io
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import openpyxl
import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from reportlab.pdfgen import canvas

from app.api import store_document
from app.common import audit
from app.database.models import AppUser, Membership
from app.domain.models import InsuredAccount, Submission, WorkItem
from app.identity import Actor, Scope, check_origin, database, set_session, settings, throttle

router = APIRouter(prefix="/api/v1", tags=["synthetic-demo"])


def pdf(lines: list[str]) -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=(612, 792))
    y = 750
    for line in lines:
        if y < 45:
            page.showPage()
            y = 750
        page.drawString(35, y, line)
        y -= 19
    page.save()
    return stream.getvalue()


def fixtures() -> list[tuple[str, bytes]]:
    year = date.today().year
    start, end = f"{year + 1}-01-01", f"{year + 1}-12-31"
    application = pdf(
        [
            "FICTIONAL CGL APPLICATION - SYNTHETIC ONLY",
            "Legal insured name: Lakeshore Design Services LLC",
            "Entity type: LLC",
            "Business operations: Interior design consulting for commercial offices",
            "Business location: 100 Fictional Way, Example City, IL 00000",
            f"Exposure period: {start} to {end}",
            "Exposure status: estimate",
            "Currency: USD",
            "Annual gross sales: 1,200,000",
            "Geographic exposure: Illinois",
            "Requested each occurrence limit: 1,000,000",
            "Requested general aggregate limit: 2,000,000",
        ]
    )
    policy = pdf(
        [
            "FICTIONAL EXPIRING POLICY DECLARATIONS - SYNTHETIC ONLY",
            "Legal insured name: Lakeshore Design Services LLC",
            "Insurer: Fictional Mutual",
            "Policy number: DEMO-CGL-1001",
            f"Policy effective: {year}-01-01",
            f"Policy expiration: {year + 1}-01-01",
            "Currency: USD",
            "Each occurrence limit: 1,000,000",
            "General aggregate limit: 2,000,000",
            "Products completed operations aggregate: 2,000,000",
            "Coverage trigger: Occurrence",
            "Endorsement: DEMO-001 - Refer to attached original wording; no interpretation supplied",
        ]
    )
    # Keep the entire claim in one cited line. The parser abstains if required components are absent.
    losses = pdf(
        [
            "FICTIONAL LOSS RUN - SYNTHETIC ONLY",
            "Currency: USD",
            f"Loss report period: {year - 2}-01-01 to {date.today().isoformat()}",
            f"Valuation date: {date.today().isoformat()}",
            f"Claim: D01 | carrier=Demo | policy=P01 | paid=100 | reserves=0 | incurred=100 | valuation={date.today().isoformat()}",
        ]
    )
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Sales schedule"
    for row in [
        ["FICTIONAL EXPOSURE SCHEDULE - SYNTHETIC ONLY"],
        ["Currency", "USD"],
        ["Exposure period", start + " to " + end],
        ["Exposure status", "estimate"],
        ["Annual gross sales", "1,350,000"],
    ]:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return [
        ("CGL-application.pdf", application),
        ("Expiring-policy.pdf", policy),
        ("Loss-runs.pdf", losses),
        ("Sales-schedule.xlsx", stream.getvalue()),
    ]


@router.post("/auth/demo", status_code=201)
def demo(request: Request, response: Response) -> dict[str, Any]:
    check_origin(request)
    throttle(request, "demo", 5)
    configuration = settings(request)
    db = database(request)
    with db.identity() as session:
        user = AppUser(
            identity_issuer="synthetic-demo",
            identity_subject=str(uuid4()),
            display_name="Alex Morgan",
            email=None,
        )
        session.add(user)
        session.flush()
        tenant_id = session.scalar(
            sa.text("SELECT app.bootstrap_agency(:user, :name)"),
            {"user": user.id, "name": "Example Brokerage · Synthetic"},
        )
        member = session.scalar(
            sa.select(Membership).where(
                Membership.user_id == user.id, Membership.tenant_id == tenant_id
            )
        )
        assert member is not None and tenant_id is not None
        auth = set_session(
            session,
            response,
            configuration,
            user.id,
            tenant_id,
            verified=True,
            method="synthetic-demo-bypass",
        )
        actor = Actor(
            user.id,
            auth.id,
            user.display_name,
            None,
            tenant_id,
            member.id,
            ("preparer", "approver", "tenant_admin"),
            frozenset({"documents.write"}),
            True,
            "synthetic-demo-bypass",
        )
    with db.tenant(tenant_id, user.id) as session:
        scope = Scope(session, actor, configuration)
        insured = InsuredAccount(
            tenant_id=tenant_id,
            name="Lakeshore Design Services LLC",
            operations="Interior design consulting for commercial offices",
            entity_type="LLC",
        )
        session.add(insured)
        session.flush()
        year = date.today().year
        submission = Submission(
            tenant_id=tenant_id,
            insured_id=insured.id,
            title=f"{year + 1} CGL renewal",
            kind="renewal",
            assigned_membership_id=member.id,
            intended_start=date(year + 1, 1, 1),
            intended_end=date(year + 2, 1, 1),
            due_date=date.today() + timedelta(days=21),
            loss_required_start=date(year - 2, 1, 1),
            loss_required_end=date.today(),
            requirements={"loss_freshness_days": 60},
        )
        session.add(submission)
        session.flush()
        for filename, data in fixtures():
            store_document(scope, submission, filename, data, uuid4(), None, trusted_fixture=True)
        session.add(
            WorkItem(
                tenant_id=tenant_id,
                submission_id=submission.id,
                title="Confirm estimated sales with the insured",
                description="The application and sales schedule have different estimates. Record the insured's confirmation before resolving the discrepancy.",
                due_date=date.today() + timedelta(days=7),
                owner_id=user.id,
            )
        )
        audit(session, tenant_id, member.id, "demo.created", "submission", submission.id)
        submission_id = submission.id
    return {
        "submission_id": str(submission_id),
        "synthetic_only": True,
        "notice": "Isolated fictional agency. Demo access bypasses MFA only for synthetic review data.",
    }
