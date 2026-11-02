"""Synthetic transport failures; no network or paid provider calls."""

import gzip
import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from app.config import Environment, Settings
from app.documents import Block, DocumentError, provider_extract
from app.provider_transport import MAX_RESPONSE_BYTES, bounded_json


class Chunks(httpx2.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def install(monkeypatch: pytest.MonkeyPatch, stream: Chunks, headers: dict[str, str]) -> None:
    original = httpx2.Client

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx2.Response(200, headers=headers, stream=stream)

    monkeypatch.setattr(
        httpx2,
        "Client",
        lambda **kwargs: original(transport=httpx2.MockTransport(handler)),
    )


def extract() -> None:
    provider_extract(
        [Block("Fictional evidence", {})],
        Settings(
            _env_file=None,
            app_env=Environment.TEST,
            extraction_provider="gateway",
            extraction_endpoint="https://provider.example.invalid/extract",
        ),
    )


@pytest.mark.parametrize("declared", [False, True])
def test_oversized_response_stops_before_decoding_and_closes(
    monkeypatch: pytest.MonkeyPatch, declared: bool
) -> None:
    stream = Chunks([b"x" * 65536] * (MAX_RESPONSE_BYTES // 65536 + 10))
    install(
        monkeypatch, stream, {"content-length": str(MAX_RESPONSE_BYTES + 1)} if declared else {}
    )
    with pytest.raises(DocumentError, match="^PROVIDER_OUTPUT_LIMIT$") as caught:
        extract()
    assert not caught.value.retryable
    assert stream.closed
    assert stream.read_count == (0 if declared else MAX_RESPONSE_BYTES // 65536 + 1)


def test_compression_is_refused_before_body_read(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = Chunks([gzip.compress(b"x" * (MAX_RESPONSE_BYTES + 1))])
    install(monkeypatch, stream, {"content-encoding": "gzip"})
    with pytest.raises(DocumentError, match="^PROVIDER_ENCODING_REJECTED$"):
        extract()
    assert stream.closed and stream.read_count == 0


def test_slow_drip_has_total_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 45.0, 91.0])
    monkeypatch.setattr("app.provider_transport.monotonic", lambda: next(clock))
    stream = Chunks([b"{", b'"facts":', b"[]}"])
    install(monkeypatch, stream, {})
    with pytest.raises(DocumentError, match="^PROVIDER_RESPONSE_TIMEOUT$") as caught:
        extract()
    assert caught.value.retryable and stream.closed and stream.read_count == 2


@pytest.mark.parametrize(
    "body,code",
    [
        (b"[" * 33 + b"]" * 33, "PROVIDER_STRUCTURE_LIMIT"),
        (b"not JSON", "PROVIDER_INVALID_RESPONSE"),
    ],
)
def test_invalid_or_deep_json_stops_before_schema_validation(
    monkeypatch: pytest.MonkeyPatch, body: bytes, code: str
) -> None:
    stream = Chunks([body])
    install(monkeypatch, stream, {})
    with pytest.raises(DocumentError, match=f"^{code}$"):
        extract()
    assert stream.closed


def test_json_brackets_inside_escaped_strings_are_not_structure() -> None:
    value: dict[str, Any] = {"text": '[\\"' * 100}
    assert bounded_json(json.dumps(value).encode()) == value


def test_socket_timeout_is_retryable_without_exposing_provider_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx2.Client

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("untrusted private provider detail")

    monkeypatch.setattr(
        httpx2,
        "Client",
        lambda **kwargs: original(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(DocumentError, match="^PROVIDER_EXTRACTION_FAILED$") as caught:
        extract()
    assert caught.value.retryable
