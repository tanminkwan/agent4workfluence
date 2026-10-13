"""FastAPI 앱. 인자 없이 부르면 환경변수로 조립한다 (uvicorn --factory)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from .api import errors
from .api.routes import public, v1
from .reviews import ReviewService
from .runner import ApplyRunner
from .service import ApplyService
from .storage.bundles import FileToolStore
from .storage.sqlite_db import SqliteManufactureDb


@dataclass(frozen=True)
class AppContext:
    runner: ApplyRunner
    reviews: ReviewService
    service: ApplyService
    store: FileToolStore
    db: SqliteManufactureDb
    auth_mode: str  # none | bearer
    api_token: str | None


def create_app(ctx: AppContext | None = None) -> FastAPI:
    if ctx is None:
        import os

        from .wiring import build_context

        ctx = build_context(os.environ)
    app = FastAPI(
        title="a4w Manufacture",
        version="0.1.0",
        description="Detection 변경분으로 작은 모델이 쓰기 좋은 선언형 tool을 만들고, Detection으로 실호출 검증을 받고, "
        "tool-store와 AI Pro Plus 색인에 반영한다. 타겟을 직접 부르지 않는다. 실행은 호출로만 일어난다.",
    )
    app.state.ctx = ctx
    errors.install(app)
    app.include_router(v1)
    app.include_router(public)
    return app
