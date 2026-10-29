import json
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr

from app.config import Environment, Settings
from app.documents import Block, DocumentError, provider_extract
from app.domain.cgl import Context


def configuration() -> Settings:
    return Settings(
        app_env=Environment.TEST,
        extraction_provider="openai",
        extraction_key=SecretStr("synthetic-not-a-real-key"),
        extraction_model="test-model",
        _env_file=None,
    )


def install_response(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = httpx2.Client

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://api.openai.com/v1/responses"
        calls.append(json.loads(request.content))
        return httpx2.Response(200, json=body)

    monkeypatch.setattr(
        "app.documents.httpx2.Client",
        lambda **kwargs: original(transport=httpx2.MockTransport(handler)),
    )
    return calls


def response(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"facts": facts})}],
            }
        ],
    }


def test_openai_structured_request_no_storage_and_supported_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = Block("Legal insured name: Fictional Company LLC", {"kind": "pdf", "page": 1})
    facts = [
        {
            "field": "insured.legal_name",
            "value_json": json.dumps("Fictional Company LLC"),
            "context": Context().model_dump(mode="json"),
            "source_ids": [str(block.id)],
        }
    ]
    calls = install_response(monkeypatch, response(facts))
    result = provider_extract([block], configuration())
    assert len(result) == 1 and result[0].value == "Fictional Company LLC"
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True
    assert "tools" not in calls[0]
    assert "untrusted" in calls[0]["instructions"]


@pytest.mark.parametrize(
    "value,citation", [("Invented Company LLC", None), ("Fictional Company LLC", str(uuid4()))]
)
def test_provider_rejects_invented_values_and_citations(
    monkeypatch: pytest.MonkeyPatch, value: str, citation: str | None
) -> None:
    block = Block("Legal insured name: Fictional Company LLC", {"kind": "pdf", "page": 1})
    facts = [
        {
            "field": "insured.legal_name",
            "value_json": json.dumps(value),
            "context": Context().model_dump(mode="json"),
            "source_ids": [citation or str(block.id)],
        }
    ]
    install_response(monkeypatch, response(facts))
    with pytest.raises(DocumentError, match="UNSUPPORTED_PROVIDER"):
        provider_extract([block], configuration())


def test_provider_refusal_and_incomplete_response_stop_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = Block("Untrusted instructions", {"kind": "pdf", "page": 1})
    install_response(monkeypatch, {"status": "incomplete", "output": []})
    with pytest.raises(DocumentError, match="PROVIDER_INCOMPLETE"):
        provider_extract([block], configuration())
