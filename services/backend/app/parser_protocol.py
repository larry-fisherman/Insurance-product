"""Closed parser error vocabulary; a remote response cannot select retry policy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ParserFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: Literal[
        "ACTIVE_PDF_CONTENT",
        "UNSAFE_SPREADSHEET",
        "PASSWORD_PROTECTED_PDF",
        "PDF_OBJECT_LIMIT",
        "PAGE_LIMIT",
        "TEXT_LIMIT",
        "PDF_PARSE_FAILED",
        "INVALID_SPREADSHEET",
        "SPREADSHEET_EXPANSION_LIMIT",
        "SHEET_LIMIT",
        "SPREADSHEET_CELL_LIMIT",
        "SPREADSHEET_METADATA_UNAVAILABLE",
        "SPREADSHEET_MERGE_LIMIT",
        "OCR_UNAVAILABLE",
        "OCR_FAILED",
        "PARSER_RESOURCE_LIMIT",
        "PARSER_INVALID_RESPONSE",
        "PARSER_CONFIGURATION",
        "PARSER_BUSY",
        "UNSUPPORTED_DOCUMENT",
        "FILE_LIMIT",
    ]


TRANSIENT_ERRORS = {"PARSER_BUSY", "PARSER_CONFIGURATION"}
