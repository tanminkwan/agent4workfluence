"""라우트 (Phase 2 설계 16절). 실행 API는 POST /v1/apply 하나다(되돌리기·보류 결정은 관리용)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from a4w_core.errors import StorageError, TargetNotFound

from .errors import Unauthorized
from .schemas import (
    ApplyAccepted,
    ApplyRequest,
    ApplyResult,
    DecisionRequest,
    ErrorBody,
    Health,
    RollbackRequest,
    ToolsetBundle,
    ToolsetPointer,
)

ERRORS = {s: {"model": ErrorBody} for s in (400, 401, 404, 409, 500, 502)}


def require_auth(request: Request) -> None:
    ctx = request.app.state.ctx
    if ctx.auth_mode != "bearer":
        return
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), (ctx.api_token or "").encode()):
        raise Unauthorized("인증 토큰이 없거나 맞지 않다")


v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_auth)])
public = APIRouter()


@v1.post(
    "/apply",
    summary="변경 반영",
    description="Detection 변경분으로 tool을 만들거나 고치고, Detection 검증 API로 실호출 검증을 받고, tool-store와 색인에 "
    "반영한다. 비동기다(202 뒤 GET /v1/applies/{applyId}). 이미 반영한 changeSetId면 앞선 결과(200). "
    "changeSetId 없이 부르면 정리 반영(승인된 보류 항목, 지난 실패 재시도, 색인 어긋남 맞추기).",
    status_code=202,
    responses={202: {"model": ApplyAccepted}, 200: {"model": ApplyResult}, **ERRORS},
)
def apply(body: ApplyRequest, request: Request):
    record, created = request.app.state.ctx.runner.start(body.targetId, body.changeSetId)
    return JSONResponse(record, status_code=202 if created else 200)


@v1.get("/applies/{applyId}", summary="반영 결과 조회", responses={200: {"model": ApplyResult}, **ERRORS})
def get_apply(applyId: str, request: Request):
    return JSONResponse(request.app.state.ctx.runner.get(applyId))


@v1.get("/reviews", summary="보류 목록", responses=ERRORS)
def list_reviews(request: Request, targetId: str = Query(), status: str | None = Query(None)):
    return {"reviews": request.app.state.ctx.reviews.list(targetId, status)}


@v1.get("/reviews/{reviewId}", summary="보류 항목", responses=ERRORS)
def get_review(reviewId: str, request: Request):
    return JSONResponse(request.app.state.ctx.reviews.get(reviewId))


@v1.post("/reviews/{reviewId}/decision", summary="보류 결정 (승인·거절)",
         description="결정만 기록한다. 승인한 항목은 다음 POST /v1/apply 때 들어간다.", responses=ERRORS)
def decide(reviewId: str, body: DecisionRequest, request: Request):
    return JSONResponse(request.app.state.ctx.reviews.decide(reviewId, body.decision, by=body.by, note=body.note))


@v1.post("/rollback", summary="되돌리기", description="서빙 포인터만 옮기고 색인을 그 버전에 맞춘다. 다음 반영은 head 위에 쌓인다.",
         responses=ERRORS)
def rollback(body: RollbackRequest, request: Request):
    return JSONResponse(request.app.state.ctx.runner.rollback(body.targetId, body.toolVersion))


@v1.get("/targets/{targetId}/state", summary="타겟 상태 (사람 확인용)", responses=ERRORS)
def target_state(targetId: str, request: Request):
    ctx = request.app.state.ctx
    if targetId not in ctx.service.targets:
        raise TargetNotFound(f"등록되지 않은 타겟: {targetId}")
    st = ctx.db.state(targetId)
    return {
        "targetId": targetId,
        "lastAppliedSpecVersion": st.last_applied_spec_version if st else None,
        "lastChangeSetId": st.last_change_set_id if st else None,
        "headToolVersion": st.head_tool_version if st else None,
        "currentToolVersion": ctx.store.current(targetId),
        "toolVersions": ctx.store.versions(targetId),
    }


# ── tool 정의 조회 (Phase 3 설계 5절). 읽기만 하고 잠금을 잡지 않는다
TOOL_VERSION = r"^tv-[0-9]{6}-[0-9a-f]{8}$"
IMMUTABLE = "public, max-age=31536000, immutable"


def _etag(version: str) -> str:
    return f'"{version}"'


@v1.get("/targets/{targetId}/toolset/current", summary="현재 tool 버전",
        description="현재 서빙 tool 버전 id만 돌려준다. ETag가 같으면(If-None-Match) 304.",
        responses={200: {"model": ToolsetPointer}, 304: {"description": "바뀌지 않음"}, **ERRORS})
def toolset_current(targetId: str, request: Request):
    pointer = request.app.state.ctx.toolsets.current(targetId)
    etag = _etag(pointer["toolVersion"])
    if etag in _if_none_match(request):
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(pointer, headers={"ETag": etag})


@v1.get("/targets/{targetId}/toolset", summary="현재 tool 묶음",
        description="현재 버전의 manifest(a4w.toolset/v1)와 tool 정의(a4w.tool/v1)를 tool-store 파일 그대로 돌려준다.",
        responses={200: {"model": ToolsetBundle}, **ERRORS})
def toolset(targetId: str, request: Request):
    bundle = request.app.state.ctx.toolsets.bundle(targetId)
    return JSONResponse(bundle, headers={"ETag": _etag(bundle["toolVersion"])})


@v1.get("/targets/{targetId}/toolsets/{toolVersion}", summary="지정 tool 묶음",
        description="지정한 버전의 묶음. 묶음은 바뀌지 않으므로 오래 캐시해도 된다.",
        responses={200: {"model": ToolsetBundle}, **ERRORS})
def toolset_version(request: Request, targetId: str, toolVersion: str = Path(pattern=TOOL_VERSION)):
    bundle = request.app.state.ctx.toolsets.bundle(targetId, toolVersion)
    return JSONResponse(bundle, headers={"ETag": _etag(toolVersion), "Cache-Control": IMMUTABLE})


def _if_none_match(request: Request) -> set[str]:
    return {t.strip() for t in request.headers.get("if-none-match", "").split(",") if t.strip()}


@public.get("/health", summary="상태 확인", responses={200: {"model": Health}, 503: {"model": Health}})
def health(request: Request):
    ctx = request.app.state.ctx
    try:
        ctx.store.check()
        ctx.db.check()
    except (OSError, StorageError):
        return JSONResponse({"status": "error", "toolStore": "error"}, status_code=503)
    return {"status": "ok", "toolStore": "ok"}
