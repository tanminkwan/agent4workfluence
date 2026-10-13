"""FastAPI 앱. 인자 없이 부르면 환경변수로 조립한다 (uvicorn --factory)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from .api import errors
from .api.routes import public, v1
from .ports import DetectionStore
from .profile import TargetProfileService
from .service import ChangeSetQuery, DetectService
from .verify.service import VerificationService


@dataclass(frozen=True)
class AppContext:
    detect: DetectService
    query: ChangeSetQuery
    store: DetectionStore
    auth_mode: str  # none | bearer
    api_token: str | None
    # Phase 2 보강 (타겟 창구). 없으면 해당 API는 501
    verify: VerificationService | None = None
    profile: TargetProfileService | None = None


def create_app(ctx: AppContext | None = None) -> FastAPI:
    if ctx is None:
        import os

        from .wiring import build_context

        ctx = build_context(os.environ)
    app = FastAPI(
        title="a4w Detection",
        version="0.1.0",
        description="타겟 API 명세를 받아 버전 관리하고 변경분을 식별한다. 타겟 창구로서 token scope를 알려 주고 "
        "tool 실호출 검증을 한다. 실행은 호출로만 일어난다.",
    )
    app.state.ctx = ctx
    errors.install(app)
    app.include_router(v1)
    app.include_router(public)
    return app
