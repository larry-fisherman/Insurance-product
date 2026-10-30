"""Strict CGL fact schemas and context-aware, deterministic comparisons."""

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.common import canonical_json, digest

PIPELINE_VERSION = "cgl-extraction-2"
RULE_VERSION = "cgl-rules-2"
REQUIREMENT_PROFILE = "cgl-synthetic-2"


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(default="all operations", min_length=1, max_length=300)
    role: Literal["existing", "requested", "historical", "actual", "estimate", "unspecified"] = (
        "unspecified"
    )
    basis: (
        Literal[
            "sales",
            "receipts",
            "payroll",
            "subcontract_cost",
            "area",
            "admissions",
            "units",
            "other",
            "unknown",
        ]
        | None
    ) = None
    unit: str | None = Field(default=None, max_length=50)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period_start: date | None = None
    period_end: date | None = None
    as_of: date | None = None
    policy_reference: str | None = Field(default=None, max_length=300)
    classification_system: str | None = Field(default=None, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)
    location_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def dates(self) -> "Context":
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("Period end must follow its start")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(min_length=1, max_length=200)
    carrier: str = Field(min_length=1, max_length=200)
    policy: str = Field(min_length=1, max_length=200)
    loss_date: date | None = None
    report_date: date | None = None
    description: str = Field(default="Not supplied", max_length=2000)
    status: Literal["open", "closed", "reopened", "unknown"] = "unknown"
    paid: Decimal | None = Field(default=None, ge=0)
    reserves: Decimal | None = Field(default=None, ge=0)
    incurred: Decimal | None = Field(default=None, ge=0)
    valuation: date

    @field_validator("paid", "reserves", "incurred", mode="before")
    @classmethod
    def exact_amount(cls, value: Any) -> Any:
        return None if value is None else normalize_money(value)


FIELD_LABELS = {
    "insured.legal_name": "Legal insured name",
    "insured.dba": "DBA",
    "insured.entity_type": "Entity type",
    "insured.mailing_address": "Mailing address",
    "insured.operations": "Business operations",
    "insured.established": "Year established",
    "operations.class_code": "Supplied GL classification",
    "operations.geography": "Geographic exposure",
    "operations.subcontracted": "Subcontracted activities",
    "location.address": "Business location",
    "exposure.amount": "Exposure",
    "coverage.each_occurrence": "Each occurrence limit",
    "coverage.general_aggregate": "General aggregate limit",
    "coverage.products_aggregate": "Products/completed operations aggregate",
    "coverage.personal_advertising": "Personal and advertising injury",
    "coverage.rented_premises": "Damage to premises rented to you",
    "coverage.medical_payments": "Medical payments",
    "coverage.deductible": "Deductible",
    "coverage.sir": "Self-insured retention",
    "coverage.trigger": "Coverage trigger",
    "coverage.endorsement": "Endorsement / exclusion",
    "coverage.additional_insured": "Additional insured requirement",
    "policy.number": "Policy number",
    "policy.carrier": "Insurer",
    "policy.effective": "Policy effective date",
    "policy.expiration": "Policy expiration date",
    "policy.referenced_endorsement": "Referenced endorsement",
    "policy.endorsement_form": "Supplied endorsement wording",
    "loss.claim": "Claim valuation",
    "loss.period": "Loss report period",
    "loss.no_losses": "Explicit no-loss statement",
}


def normalize_money(value: str | int | float | Decimal) -> str:
    if isinstance(value, float):
        raise ValueError("Financial values must use exact decimal strings")
    cleaned = re.sub(r"[$,\s]", "", str(value))
    if not re.fullmatch(r"\d+(\.\d{1,4})?", cleaned):
        raise ValueError("Enter an exact nonnegative numeric amount")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("Invalid numeric amount") from None
    if not amount.is_finite() or amount > Decimal("1000000000000000"):
        raise ValueError("Amount exceeds the supported range")
    return format(amount.normalize(), "f")


def validate_fact(field: str, value: Any, context: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    if field not in FIELD_LABELS:
        raise ValueError("Unsupported CGL field")
    parsed = Context.model_validate(context)
    if field == "exposure.amount":
        if not parsed.basis or not parsed.unit or not parsed.period_start or not parsed.period_end:
            raise ValueError("Exposures require a basis, unit, and period")
        value = normalize_money(value)
        if (
            parsed.basis in {"sales", "receipts", "payroll", "subcontract_cost"}
            and not parsed.currency
        ):
            raise ValueError("Monetary exposures require a currency")
        if parsed.basis in {
            "sales",
            "receipts",
            "payroll",
            "subcontract_cost",
        } and parsed.unit not in {"money", parsed.currency}:
            raise ValueError("Monetary exposure unit must match its currency or be 'money'")
        if parsed.basis in {"sales", "receipts", "payroll", "subcontract_cost"}:
            parsed.unit = "money"
    elif field.startswith("coverage.") and field not in {
        "coverage.trigger",
        "coverage.endorsement",
        "coverage.additional_insured",
    }:
        if not parsed.currency or parsed.role not in {"existing", "requested", "historical"}:
            raise ValueError("Coverage amounts require a currency and existing/requested context")
        value = normalize_money(value)
    elif field == "loss.claim":
        value = Claim.model_validate(value).model_dump(mode="json")
        if not parsed.currency:
            raise ValueError("Claims require a currency")
    elif field in {"policy.effective", "policy.expiration"}:
        value = date.fromisoformat(str(value)).isoformat()
    elif field == "loss.no_losses":
        if not parsed.period_start or not parsed.period_end:
            raise ValueError("No-loss statements require an explicit report period")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Preserve the explicit no-loss statement")
    elif field == "loss.period":
        if not parsed.period_start or not parsed.period_end or not parsed.as_of:
            raise ValueError("Loss periods require beginning/end and valuation dates")
        value = "Loss report supplied"
    else:
        if not isinstance(value, str) or not value.strip() or len(value) > 8000:
            raise ValueError("A nonempty text value is required")
        value = value.strip()
    return value, parsed.model_dump(mode="json", exclude_none=True)


def group_key(field: str, context: dict[str, Any], value: Any = None) -> str:
    selected = dict(context)
    if (
        field == "exposure.amount"
        and context.get("basis") in {"sales", "receipts", "payroll", "subcontract_cost"}
        and context.get("unit") in {"money", context.get("currency")}
    ):
        selected["unit"] = "money"
    if field.startswith("insured."):
        selected = {"scope": context.get("scope", "all operations")}
        if context.get("entity_id"):
            selected["entity_id"] = context["entity_id"]
    if field in {
        "coverage.endorsement",
        "coverage.additional_insured",
        "insured.dba",
        "operations.class_code",
        "policy.referenced_endorsement",
        "policy.endorsement_form",
    } and not (field == "insured.dba" and context.get("entity_id")):
        selected["item"] = value
    if field == "location.address":
        selected = {"location_id": context.get("location_id") or digest(canonical_json(value))[:20]}
    if field == "loss.claim" and isinstance(value, dict):
        selected["claim_identity"] = [
            value.get("carrier"),
            value.get("policy"),
            value.get("claim_id"),
        ]
        selected["valuation"] = value.get("valuation")
    return field + ":" + digest(canonical_json(selected))[:20]


def fingerprint(observations: list[dict[str, Any]]) -> str:
    return digest(
        canonical_json(
            sorted(
                [
                    (
                        str(o["id"]),
                        o["value"],
                        o["context"],
                        o.get("availability", "available"),
                        o.get("support_status", "supported"),
                    )
                    for o in observations
                ],
                key=lambda x: x[0],
            )
        )
    )


def reconcile(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        groups[observation["group_key"]].append(observation)
    results: list[dict[str, Any]] = []
    for key, items in groups.items():
        values = {canonical_json(item["value"]) for item in items}
        if len(values) > 1:
            field = items[0]["field"]
            results.append(
                {
                    "key": key,
                    "kind": "conflict",
                    "severity": "material",
                    "message": f"Conflicting {FIELD_LABELS[field].lower()} values for the same context. Broker resolution required.",
                    "observation_ids": [str(o["id"]) for o in items],
                    "input_fingerprint": fingerprint(items),
                }
            )
    return results


def suggested_question(issue: str) -> str:
    return (
        "Please confirm "
        + issue[:400].rstrip(".").lower()
        + ", and provide supporting documentation."
    )
