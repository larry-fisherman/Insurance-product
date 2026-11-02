"""Worker transport; parsed content remains unverified evidence."""

import httpx2
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.config import Settings
from app.documents import MAX_PAGES, MAX_TEXT_CHARS, Block, DocumentError, parse_pdf, parse_xlsx
from app.parser_protocol import TRANSIENT_ERRORS, ParserFailure


class ParsedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=MAX_TEXT_CHARS)
    locator: dict[str, JsonValue]


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[ParsedBlock] = Field(max_length=100000)
    page_count: int = Field(ge=0, le=MAX_PAGES)


def parse_document(
    data: bytes, media_type: str, configuration: Settings
) -> tuple[list[Block], int]:
    if not configuration.parser_endpoint:
        # Explicit local-test fallback; compose always supplies the isolated service.
        return parse_pdf(data) if media_type == "application/pdf" else parse_xlsx(data)
    try:
        with (
            httpx2.Client(timeout=260, follow_redirects=False) as client,
            client.stream(
                "POST",
                configuration.parser_endpoint.rstrip("/") + "/parse",
                content=data,
                headers={"Content-Type": media_type},
            ) as response,
        ):
            body = bytearray()
            output_limit = 12 * 1024 * 1024 if response.status_code == 200 else 4096
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > output_limit:
                    raise DocumentError("PARSER_OUTPUT_LIMIT")
            if response.status_code != 200:
                try:
                    failure = ParserFailure.model_validate_json(body)
                except ValidationError:
                    raise DocumentError(
                        "PARSER_UNAVAILABLE"
                        if response.status_code >= 500
                        else "PARSER_INVALID_RESPONSE",
                        retryable=response.status_code >= 500,
                    ) from None
                raise DocumentError(
                    failure.error,
                    retryable=failure.error in TRANSIENT_ERRORS and response.status_code == 503,
                )
        parsed = ParsedDocument.model_validate_json(body)
        if sum(len(block.text) for block in parsed.blocks) > MAX_TEXT_CHARS:
            raise DocumentError("TEXT_LIMIT")
        return [
            Block(block.text, dict(block.locator)) for block in parsed.blocks
        ], parsed.page_count
    except (httpx2.HTTPError, ValidationError) as error:
        raise DocumentError("PARSER_UNAVAILABLE", retryable=True) from error
