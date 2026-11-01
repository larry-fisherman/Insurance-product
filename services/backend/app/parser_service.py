"""Credential-free parsing service; full-stack network access is worker-only."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

MAX_INPUT = 50 * 1024 * 1024
MAX_OUTPUT = 12 * 1024 * 1024


def run_parser(data: bytes, media_type: str) -> dict[str, Any]:
    # The child sees only this file, a private scratch directory, and a minimal environment.
    with TemporaryDirectory(prefix="cgl-parse-") as temporary:
        directory = Path(temporary)
        (directory / "input").write_bytes(data)
        with (directory / "output").open("wb") as output:
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", "-m", "app.parser_runner", media_type],
                    cwd=directory,
                    env={
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "LANG": "C.UTF-8",
                        "TMPDIR": temporary,
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                return {"error": "PARSER_RESOURCE_LIMIT"}
        if completed.returncode or (directory / "output").stat().st_size > MAX_OUTPUT:
            return {"error": "PARSER_RESOURCE_LIMIT"}
        try:
            result = json.loads((directory / "output").read_bytes())
            if isinstance(result, dict):
                return result
        except (ValueError, OSError):
            pass
        return {"error": "PARSER_INVALID_RESPONSE"}


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    semaphore = asyncio.Semaphore(1)

    @app.get("/health")
    def health() -> dict[str, bool]:
        # Fail if someone accidentally gives this process operational credentials.
        forbidden = ("CGL_", "AWS_", "OPENAI_")
        return {"ready": not any(key.startswith(forbidden) for key in os.environ)}

    @app.post("/parse")
    async def parse(request: Request) -> JSONResponse:
        if not health()["ready"]:
            return JSONResponse({"error": "PARSER_CONFIGURATION"}, status_code=503)
        media_type = request.headers.get("content-type", "")
        if media_type not in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            return JSONResponse({"error": "UNSUPPORTED_DOCUMENT"}, status_code=415)
        length = request.headers.get("content-length", "")
        if not length.isdigit() or not 1 <= int(length) <= MAX_INPUT:
            return JSONResponse({"error": "FILE_LIMIT"}, status_code=413)
        if semaphore.locked():
            return JSONResponse({"error": "PARSER_BUSY"}, status_code=503)
        async with semaphore:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_INPUT:
                    return JSONResponse({"error": "FILE_LIMIT"}, status_code=413)
            result = await asyncio.to_thread(run_parser, bytes(data), media_type)
        return JSONResponse(result, status_code=422 if "error" in result else 200)

    return app
