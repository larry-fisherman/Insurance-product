"""The isolated service must preserve security decisions across its HTTP boundary."""

import io
import os
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.actions import JavaScript

from app import parser_client, parser_service
from app.config import Environment, Settings
from app.documents import DocumentError


def configuration() -> Settings:
    return Settings(_env_file=None, app_env=Environment.TEST, parser_endpoint="http://parser:8020")


@pytest.mark.parametrize(
    "status,body,code,retryable",
    [
        (422, b'{"error":"ACTIVE_PDF_CONTENT"}', "ACTIVE_PDF_CONTENT", False),
        (422, b'{"error":"UNSAFE_SPREADSHEET"}', "UNSAFE_SPREADSHEET", False),
        (422, b'{"error":"PASSWORD_PROTECTED_PDF"}', "PASSWORD_PROTECTED_PDF", False),
        (422, b'{"error":"PARSER_RESOURCE_LIMIT"}', "PARSER_RESOURCE_LIMIT", False),
        (503, b'{"error":"PARSER_BUSY"}', "PARSER_BUSY", True),
        (503, b'{"error":"ACTIVE_PDF_CONTENT"}', "ACTIVE_PDF_CONTENT", False),
        (422, b'{"error":"arbitrary provider text"}', "PARSER_INVALID_RESPONSE", False),
        (422, b'{"error":"ACTIVE_PDF_CONTENT","retryable":true}', "PARSER_INVALID_RESPONSE", False),
        (422, b"not JSON", "PARSER_INVALID_RESPONSE", False),
        (502, b"proxy unavailable", "PARSER_UNAVAILABLE", True),
    ],
)
def test_parser_error_allowlist(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, code: str, retryable: bool
) -> None:
    original = httpx2.Client
    monkeypatch.setattr(
        httpx2,
        "Client",
        lambda **kwargs: original(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(status, content=body))
        ),
    )
    with pytest.raises(DocumentError) as caught:
        parser_client.parse_document(b"fictional", "application/pdf", configuration())
    assert caught.value.code == code
    assert caught.value.retryable is retryable


def test_active_pdf_rejection_survives_real_service_and_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(os.environ):
        if name.startswith(("CGL_", "AWS_", "OPENAI_")):
            monkeypatch.delenv(name)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_open_action(JavaScript("app.alert('fictional hostile fixture')"))
    body = io.BytesIO()
    writer.write(body)
    original = httpx2.Client
    with TestClient(parser_service.create_app()) as service:

        def transport(request: httpx2.Request) -> httpx2.Response:
            result = service.post(
                "/parse", content=request.content, headers={"Content-Type": "application/pdf"}
            )
            return httpx2.Response(result.status_code, content=result.content)

        def client(**kwargs: Any) -> httpx2.Client:
            return original(transport=httpx2.MockTransport(transport))

        monkeypatch.setattr(httpx2, "Client", client)
        with pytest.raises(DocumentError, match="^ACTIVE_PDF_CONTENT$") as caught:
            parser_client.parse_document(body.getvalue(), "application/pdf", configuration())
        assert not caught.value.retryable
