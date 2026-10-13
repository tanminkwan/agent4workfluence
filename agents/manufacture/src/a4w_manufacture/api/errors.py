"""오류 → HTTP 응답 (Phase 2 설계 16.4절). 모양은 {"error":{"code","message","details"?}} 하나다."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from a4w_core.errors import A4WError

log = logging.getLogger(__name__)

CODE_STATUS = {
    "VALIDATION_ERROR": 400,
    "UNAUTHORIZED": 401,
    "TARGET_NOT_FOUND": 404,
    "CHANGESET_NOT_FOUND": 404,
    "APPLY_NOT_FOUND": 404,
    "REVIEW_NOT_FOUND": 404,
    "TOOL_VERSION_NOT_FOUND": 404,
    "CHANGESET_OUT_OF_ORDER": 409,
    "APPLY_IN_PROGRESS": 409,
    "REVIEW_ALREADY_DECIDED": 409,
    "DETECTION_UNAVAILABLE": 502,
    "SCOPE_UNKNOWN": 502,
    "STORAGE_ERROR": 500,
    "CONFIG_ERROR": 500,
    "INTERNAL_ERROR": 500,
}


class Unauthorized(A4WError):
    code = "UNAUTHORIZED"


def error_response(code: str, message: str, status: int | None = None, details=None) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status or CODE_STATUS.get(code, 500))


def install(app: FastAPI) -> None:
    @app.exception_handler(A4WError)
    async def _a4w(request: Request, exc: A4WError):
        if CODE_STATUS.get(exc.code, 500) >= 500:
            log.warning("request failed: code=%s path=%s", exc.code, request.url.path)
        return error_response(exc.code, exc.message, details=getattr(exc, "details", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
        return error_response("VALIDATION_ERROR", "요청 모양이 맞지 않다: " + ", ".join(fields))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unexpected error: path=%s", request.url.path)
        return error_response("INTERNAL_ERROR", "내부 오류", 500)
