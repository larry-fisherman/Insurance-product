"""Bounded, uncompressed provider responses before JSON decoding.

Compression is refused before decoding so compressed bombs cannot allocate an
unbounded intermediate buffer inside the HTTP client. These are transport limits,
not provider cost budgets or permission to send customer documents.
"""

import json
from time import monotonic
from typing import Any

import httpx2

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_RESPONSE_SECONDS = 90


class ProviderResponseError(ValueError):
    pass


def bounded_json(data: bytes) -> Any:
    if len(data) > MAX_RESPONSE_BYTES:
        raise ProviderResponseError("PROVIDER_OUTPUT_LIMIT")
    # Inspect structure before the JSON decoder allocates nested objects. Brackets
    # inside escaped JSON strings are data, not structure.
    depth, quoted, escaped = 0, False, False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ProviderResponseError("PROVIDER_STRUCTURE_LIMIT")
        elif char in (93, 125):
            depth -= 1
    try:
        # Require UTF-8 instead of allowing json.loads(bytes) to auto-detect
        # encodings whose escaped characters differ from the structural scan.
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise ProviderResponseError("PROVIDER_INVALID_RESPONSE") from None


def request_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any]) -> Any:
    deadline = monotonic() + MAX_RESPONSE_SECONDS
    with (
        httpx2.Client(
            timeout=httpx2.Timeout(90, connect=10, write=30), follow_redirects=False
        ) as client,
        client.stream(
            "POST", endpoint, headers={**headers, "Accept-Encoding": "identity"}, json=payload
        ) as response,
    ):
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise ProviderResponseError("PROVIDER_ENCODING_REJECTED")
        length = response.headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                raise ProviderResponseError("PROVIDER_INVALID_RESPONSE")
            if int(length) > MAX_RESPONSE_BYTES:
                raise ProviderResponseError("PROVIDER_OUTPUT_LIMIT")
        body = bytearray()
        for chunk in response.iter_raw():
            if monotonic() > deadline:
                raise ProviderResponseError("PROVIDER_RESPONSE_TIMEOUT")
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ProviderResponseError("PROVIDER_OUTPUT_LIMIT")
            body.extend(chunk)
        if monotonic() > deadline:
            raise ProviderResponseError("PROVIDER_RESPONSE_TIMEOUT")
    return bounded_json(bytes(body))
