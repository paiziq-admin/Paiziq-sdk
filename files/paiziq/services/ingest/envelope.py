"""Control-plane response envelope and error type.

Per docs/06_API_CONTRACT.md §1.3: control-plane endpoints wrap
responses in {"success", "data", "error", "meta"}. The frozen
ingest-plane endpoints keep their raw shapes and FastAPI's default
{"detail": ...} errors — install_error_handlers only intercepts
ApiError, so it never touches them.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Control-plane error carrying an HTTP status and machine code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def ok(data: Any, meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body: dict[str, Any] = {"success": True, "data": data, "error": None}
    if meta is not None:
        body["meta"] = meta
    return body


def list_meta(total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"total": total, "limit": limit, "offset": offset}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "data": None,
                "error": {"code": exc.code, "message": exc.message},
            },
        )
