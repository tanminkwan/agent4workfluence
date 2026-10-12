"""라우트 (설계 9절)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from a4w_core.errors import StorageError

from . import serialize
from .errors import Unauthorized
from .schemas import ChangeSetBody, DetectChanged, DetectRequest, DetectUnchanged, ErrorBody, Health

ERRORS = {s: {"model": ErrorBody} for s in (400, 401, 404, 409, 500, 501, 502)}


def require_auth(request: Request) -> None:
    ctx = request.app.state.ctx
    if ctx.auth_mode != "bearer":
        return
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), (ctx.api_token or "").encode()):
        raise Unauthorized("인증 토큰이 없거나 맞지 않다")


v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_auth)])
public = APIRouter()


@v1.post(
    "/detect",
    summary="변경 식별",
    description="타겟 명세의 변경을 확인하고, 바뀌었으면 새 버전으로 보관하고 변경분을 만든다.",
    responses={200: {"model": DetectChanged | DetectUnchanged}, **ERRORS},
)
def detect(body: DetectRequest, request: Request):
    result = request.app.state.ctx.detect.detect(body.targetId)
    return JSONResponse(serialize.detect_result(result))


@v1.get(
    "/changesets/{changeSetId}",
    summary="변경분 조회",
    description="변경분 ID로 동작 단위 변경 목록을 돌려준다. Detection–Manufacture 계약이다.",
    responses={200: {"model": ChangeSetBody}, **ERRORS},
)
def get_changeset(
    changeSetId: str,
    request: Request,
    targetId: str = Query(description="변경분의 타겟 ID"),
    includeTextDiff: bool = Query(False, description="줄 단위 diff를 담을지"),
):
    cs = request.app.state.ctx.query.get(targetId, changeSetId)
    return JSONResponse(serialize.changeset(cs, include_text_diff=includeTextDiff))


@public.get("/health", summary="상태 확인", responses={200: {"model": Health}, 503: {"model": Health}})
def health(request: Request):
    try:
        request.app.state.ctx.store.check()
    except StorageError:
        return JSONResponse({"status": "error", "specStore": "error"}, status_code=503)
    return {"status": "ok", "specStore": "ok"}
