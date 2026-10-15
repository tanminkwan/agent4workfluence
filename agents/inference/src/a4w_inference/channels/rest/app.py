"""REST 채널 (Phase 4 설계 5절). 얇은 어댑터다: 요청을 RequestRunner에 접수하고 기록을 JSON으로 돌려준다.

수행 로직·저장소 구현을 모른다. 오류 모양 {"error":{"code","message"}}, /health, /openapi.json은 다른 서비스와 같다.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...records import RequestRecord
from ...runner import RequestRunner, SubmitError, SubmitRequest

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestContext:
    runner: RequestRunner
    auth_mode: str                       # none | bearer
    api_token: str | None
    health: Callable[[], dict[str, Any]]  # agent별 MCP 상태 (바깥 시스템 상태는 넣지 않는다)
    lifecycle: Callable[[], AbstractAsyncContextManager] | None = None   # 기동·종료 때 할 일(MCP 자식, 만료 삭제)


class Unauthorized(Exception):
    pass


class RequestBody(BaseModel):
    agentId: str
    text: str
    requester: str
    conversationId: str | None = None
    idempotencyKey: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


ERRORS = {s: {"model": ErrorBody} for s in (400, 401, 404, 409, 429)}


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def require_auth(request: Request) -> None:
    ctx: RestContext = request.app.state.ctx
    if ctx.auth_mode != "bearer":
        return
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), (ctx.api_token or "").encode()):
        raise Unauthorized()


def _view(rec: RequestRecord) -> dict[str, Any]:
    u = rec.usage
    return {
        "requestId": rec.request_id, "agentId": rec.agent_id, "conversationId": rec.conversation_id, "status": rec.status,
        "complete": rec.complete, "stopReason": rec.stop_reason, "answer": rec.answer,
        "actions": [a.to_dict() for a in rec.actions],
        "usage": {"promptTokens": u.prompt_tokens, "completionTokens": u.completion_tokens, "reasoningTokens": u.reasoning_tokens,
                  "steps": u.steps, "toolCalls": u.tool_calls, "writeCalls": u.write_calls, "lastInputTokens": u.last_input_tokens,
                  "lastInputChars": u.last_input_chars, "historyDroppedTurns": u.history_dropped_turns,
                  "trimmedResults": u.trimmed_results},
        "elapsedMs": rec.elapsed_ms, "createdAt": rec.created_at, "finishedAt": rec.finished_at,
        "error": {"code": rec.error_code, "message": rec.error_message} if rec.status == "failed" else None,
    }


v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_auth)])
public = APIRouter()


@v1.post("/requests", status_code=202, responses=ERRORS, summary="요청 접수",
         description="text 요청을 접수하고 202와 requestId를 준다. 수행은 따로 돌고 결과는 GET /v1/requests/{requestId}로 본다. "
         "필수 값이 모자라면 agent가 tool을 부르지 않고 되묻는 답으로 끝난다. 같은 conversationId로 값을 더해 다시 보내면 앞선 대화를 이어 받는다. "
         "같은 idempotencyKey·text의 재전송은 200으로 앞선 요청을 돌려준다.")
async def submit(body: RequestBody, request: Request):
    runner = request.app.state.ctx.runner
    accepted = await runner.submit(SubmitRequest(agent_id=body.agentId, text=body.text, requester=body.requester,
                                                 conversation_id=body.conversationId, idempotency_key=body.idempotencyKey))
    return JSONResponse({"requestId": accepted.request_id, "status": accepted.status, "conversationId": accepted.conversation_id},
                        status_code=200 if accepted.replayed else 202)


@v1.get("/requests/{requestId}", responses=ERRORS, summary="요청 결과 조회")
async def get_request(requestId: str, request: Request):
    rec = request.app.state.ctx.runner.get(requestId)
    if rec is None:
        return error_response("REQUEST_NOT_FOUND", "없는 요청이다", 404)
    return _view(rec)


@public.get("/health", summary="상태 확인")
async def health(request: Request):
    return {"status": "ok", "agents": request.app.state.ctx.health()}


def install_errors(app: FastAPI) -> None:
    @app.exception_handler(SubmitError)
    async def _submit(request: Request, exc: SubmitError):
        return error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(Unauthorized)
    async def _unauthorized(request: Request, exc: Unauthorized):
        return error_response("UNAUTHORIZED", "인증 토큰이 없거나 맞지 않다", 401)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
        return error_response("VALIDATION_ERROR", "요청 모양이 맞지 않다: " + ", ".join(fields), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.error("unexpected error: path=%s type=%s", request.url.path, type(exc).__name__)
        return error_response("INTERNAL_ERROR", "내부 오류", 500)


def create_app(ctx: RestContext | None = None) -> FastAPI:
    if ctx is None:                      # uvicorn --factory: 환경변수로 조립한다
        import os

        from ...wiring import build_rest_context

        ctx = build_rest_context(os.environ)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if ctx.lifecycle is None:
            yield
        else:
            async with ctx.lifecycle():
                yield

    app = FastAPI(
        lifespan=lifespan,
        title="a4w Inference",
        version="0.1.0",
        description="text 요청을 받아 tool을 찾아(AI Pro Plus) 모델이 고른 tool을 공용 MCP server로 실행하고 결과를 돌려준다. "
        "비동기(202 + 조회)이고, 값이 모자라면 tool을 부르지 않고 되묻는 답으로 끝난다. 같은 conversationId로 이어 갈 수 있다.",
    )
    app.state.ctx = ctx
    install_errors(app)
    app.include_router(v1)
    app.include_router(public)
    return app
