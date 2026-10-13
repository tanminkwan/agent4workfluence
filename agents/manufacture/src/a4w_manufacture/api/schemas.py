"""요청·응답 모양 (Phase 2 설계 16절). 응답 본문은 서비스가 만들고, 이 모델은 검증과 OpenAPI 문서에 쓴다."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    targetId: str = Field(min_length=1, description="반영할 타겟 ID")
    changeSetId: str | None = Field(None, min_length=1,
                                    description="Detection 변경분 ID. 없으면 정리 반영(승인 항목·지난 실패·색인 어긋남)")


class ApplyAccepted(BaseModel):
    status: Literal["running"]
    applyId: str = Field(description="결과는 GET /v1/applies/{applyId}")
    targetId: str
    changeSetId: str | None


class ApplyResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    applyId: str
    status: Literal["running", "applied", "partial", "noop", "failed"]
    targetId: str
    toolVersion: str | None = None
    summary: dict[str, int] | None = None
    items: list[dict[str, Any]] | None = None
    pendingReview: list[dict[str, Any]] | None = None
    codeOwnedChanges: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    by: str | None = Field(None, max_length=100)
    note: str | None = Field(None, max_length=2000)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    targetId: str = Field(min_length=1)
    toolVersion: str = Field(pattern=r"^tv-[0-9]{6}-[0-9a-f]{8}$")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorBody(BaseModel):
    error: ErrorDetail


class Health(BaseModel):
    status: Literal["ok", "error"]
    toolStore: Literal["ok", "error"]
