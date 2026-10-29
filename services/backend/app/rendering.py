"""Original submission templates. Rendering consumes only an immutable reviewed snapshot."""

import io
import json
import zipfile
from html import escape
from typing import Any

import openpyxl
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.common import canonical_json, digest
from app.domain.cgl import FIELD_LABELS

TEMPLATE_VERSION = "original-cgl-1"


def text(value: Any) -> str:
    return canonical_json(value) if isinstance(value, (dict, list)) else str(value)


def safe_cell(value: Any) -> str:
    rendered = text(value)
    return "'" + rendered if rendered.startswith(("=", "+", "-", "@", "\t", "\r")) else rendered


def render(
    snapshot: dict[str, Any],
    revision_hash: str,
    documents: list[dict[str, Any]],
    supporting: list[tuple[str, bytes]],
    evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, tuple[bytes, str, str]], dict[str, Any]]:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#18332e")
    story: list[Any] = []

    def paragraph(value: str, style: str = "BodyText") -> None:
        story.append(Paragraph(escape(value).replace("\n", "<br/>"), styles[style]))
        story.append(Spacer(1, 7))

    paragraph("Commercial General Liability", "Title")
    paragraph("Broker submission package · Synthetic review build", "Heading2")
    paragraph(f"Proposed period: {snapshot['intended_start']} to {snapshot['intended_end']}")
    paragraph(
        "This original template summarizes broker-reviewed facts. Policy wording, endorsements, and the attached documents control. This software does not recommend coverage or bind insurance."
    )
    facts = snapshot["facts"]
    sources = {source["id"]: source for source in (evidence or [])}
    document_names = {d["id"]: d["filename"] for d in documents}

    def citation(source_id: str) -> str:
        source = sources.get(source_id)
        if not source:
            return source_id
        locator = source["locator"]
        location = (
            f"page {locator.get('page')}, line {locator.get('line')}"
            if locator["kind"] == "pdf"
            else f"{locator.get('sheet')} {locator.get('range')}"
        )
        return f"{document_names.get(source['document_id'], source['document_id'])} ({location}; source {source_id})"

    for prefix, heading in [
        ("insured.", "Insured overview"),
        ("location.", "Locations"),
        ("operations.", "Operations and classifications"),
        ("exposure.", "GL exposures"),
        ("policy.", "Existing policy"),
        ("coverage.", "Existing and requested coverage"),
        ("loss.", "Loss history"),
    ]:
        paragraph(heading, "Heading2")
        selected = [fact for fact in facts if fact["field"].startswith(prefix)]
        if not selected:
            paragraph(
                "Not supplied or not verified. No coverage or no-loss conclusion is inferred."
            )
        for fact in selected:
            paragraph(FIELD_LABELS[fact["field"]] + ": " + text(fact["value"]), "Heading3")
            paragraph("Context: " + text(fact["context"]))
            paragraph(
                "Evidence: "
                + ("; ".join(citation(ref) for ref in fact["source_ids"]) or "Broker attestation")
                + "; review decision "
                + fact["decision_id"]
            )
            paragraph("Resolution: " + fact["reason"])
    paragraph("Underwriting narrative", "Heading2")
    names = [text(f["value"]) for f in facts if f["field"] == "insured.legal_name"]
    operations = [text(f["value"]) for f in facts if f["field"] == "insured.operations"]
    paragraph(
        "Insured: "
        + "; ".join(names)
        + ". Operations as verified by the broker: "
        + "; ".join(operations)
        + ". Refer to the exposure, coverage, and loss sections for their specific periods and contexts."
    )
    paragraph("Exceptions and omissions", "Heading2")
    for item in snapshot["exceptions"]:
        paragraph(item["message"] + " Reason: " + str(item.get("reason", "")))
    for item in snapshot["omissions"]:
        paragraph("Omitted review group " + item["group_key"] + ": " + item["reason"])
    if not snapshot["exceptions"] and not snapshot["omissions"]:
        paragraph("No accepted exceptions or omitted extracted fields in this revision.")
    paragraph("Supporting document index", "Heading2")
    for document in documents:
        paragraph(f"{document['id']} · {document['filename']} · SHA-256 {document['sha256']}")
    paragraph("Reviewed revision SHA-256: " + revision_hash)
    paragraph(
        "Template: "
        + TEMPLATE_VERSION
        + ". Approval is recorded separately against this exact package manifest. Candidate previews are unapproved until broker approval."
    )
    SimpleDocTemplate(
        buffer,
        pagesize=(8.5 * inch, 11 * inch),
        leftMargin=45,
        rightMargin=45,
        topMargin=40,
        bottomMargin=40,
        title="CGL broker submission",
    ).build(story)
    pdf = buffer.getvalue()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Reviewed facts"
    sheet.append(
        ["Field", "Value (exact text)", "Context", "Evidence IDs", "Decision ID", "Reason"]
    )
    for fact in facts:
        sheet.append(
            [
                safe_cell(fact["field"]),
                safe_cell(fact["value"]),
                safe_cell(fact["context"]),
                safe_cell(
                    "; ".join(citation(ref) for ref in fact["source_ids"]) or "Broker attestation"
                ),
                safe_cell(fact["decision_id"]),
                safe_cell(fact["reason"]),
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in {"A": 32, "B": 55, "C": 65, "D": 55, "E": 40, "F": 60}.items():
        sheet.column_dimensions[column].width = width
    notes = workbook.create_sheet("Exceptions")
    notes.append(["Requirement", "Message", "Reason"])
    for item in snapshot["exceptions"]:
        notes.append(
            [safe_cell(item["key"]), safe_cell(item["message"]), safe_cell(item.get("reason", ""))]
        )
    for item in snapshot["omissions"]:
        notes.append([safe_cell(item["group_key"]), "Omitted", safe_cell(item["reason"])])
    index = workbook.create_sheet("Document index")
    index.append(["Document ID", "Filename", "SHA-256"])
    for doc in documents:
        index.append([safe_cell(doc["id"]), safe_cell(doc["filename"]), doc["sha256"]])
    source_sheet = workbook.create_sheet("Source references")
    source_sheet.append(["Source ID", "Document", "Location", "Quoted text"])
    for source in evidence or []:
        source_sheet.append(
            [
                source["id"],
                safe_cell(document_names.get(source["document_id"], source["document_id"])),
                safe_cell(source["locator"]),
                safe_cell(source["text"]),
            ]
        )
    xlsx_buffer = io.BytesIO()
    workbook.save(xlsx_buffer)
    workbook.close()
    xlsx = xlsx_buffer.getvalue()
    manifest = {
        "template_version": TEMPLATE_VERSION,
        "revision_hash": revision_hash,
        "documents": documents,
        "source_index": evidence or [],
        "files": [
            {"filename": name, "sha256": digest(data)}
            for name, data in [("submission.pdf", pdf), ("submission.xlsx", xlsx), *supporting]
        ],
    }
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("submission.pdf", pdf)
        archive.writestr("submission.xlsx", xlsx)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("reviewed-snapshot.json", canonical_json(snapshot))
        for name, data in supporting:
            archive.writestr(name, data)
    result = {
        "pdf": (pdf, "submission.pdf", "application/pdf"),
        "xlsx": (
            xlsx,
            "submission.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        "zip": (zip_buffer.getvalue(), "submission.zip", "application/zip"),
    }
    # The outer approval manifest also binds the archive, without a self-referential archive hash.
    manifest["artifacts"] = [
        {"format": format, "sha256": digest(data), "byte_size": len(data)}
        for format, (data, _, _) in result.items()
    ]
    return result, manifest
