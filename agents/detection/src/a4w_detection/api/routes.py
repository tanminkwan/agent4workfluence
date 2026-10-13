"""라우트 (설계 9절)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from a4w_core.errors import StorageError, UnsupportedSandbox

from . import serialize
from .errors import Unauthorized
from .schemas import (
    ChangeSetBody, DetectChanged, DetectRequest, DetectUnchanged, ErrorBody, Health, ProfileBody, VerificationAccepted,
    VerificationBody, VerificationRequest,
)

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


def _verify(request: Request):
    svc = request.app.state.ctx.verify
    if svc is None:
        raise UnsupportedSandbox("검증이 설정되지 않았다")
    return svc


@v1.get(
    "/targets/{targetId}/profile",
    summary="타겟 정보",
    description="타겟 token의 scope와 검증 환경 사용 가능 여부. token 값은 내주지 않는다.",
    responses={200: {"model": ProfileBody}, **ERRORS},
)
def target_profile(targetId: str, request: Request):
    profile = request.app.state.ctx.profile
    if profile is None:
        raise UnsupportedSandbox("타겟 정보가 설정되지 않았다")
    return JSONResponse(profile.profile(targetId))


@v1.post(
    "/verifications",
    summary="실호출 검증 접수",
    description="tool 정의와 시나리오를 받아 시험 환경에서 실행한다. 비동기다(202 뒤 조회). "
    "같은 verificationId로 다시 보내면 새로 실행하지 않고 그 상태를 돌려준다(200).",
    status_code=202,
    responses={202: {"model": VerificationAccepted}, 200: {"model": VerificationBody}, **ERRORS},
)
def submit_verification(body: VerificationRequest, request: Request):
    record, created = _verify(request).submit(body.model_dump())
    if created:
        return JSONResponse(
            {"verificationId": record["verificationId"], "targetId": record["targetId"], "status": "running"},
            status_code=202,
        )
    return JSONResponse(record, status_code=200)


@v1.get(
    "/verifications/{verificationId}",
    summary="실호출 검증 조회",
    responses={200: {"model": VerificationBody}, **ERRORS},
)
def get_verification(verificationId: str, request: Request, targetId: str = Query(description="검증의 타겟 ID")):
    return JSONResponse(_verify(request).get(targetId, verificationId))


@public.get("/health", summary="상태 확인", responses={200: {"model": Health}, 503: {"model": Health}})
def health(request: Request):
    try:
        request.app.state.ctx.store.check()
    except StorageError:
        return JSONResponse({"status": "error", "specStore": "error"}, status_code=503)
    return {"status": "ok", "specStore": "ok"}
