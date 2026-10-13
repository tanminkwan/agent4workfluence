"""요청·응답 모양 (설계 9절). 응답은 serialize.py가 만들고, 이 모델은 검증과 OpenAPI 문서에 쓴다."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DetectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    targetId: str = Field(description="변경을 식별할 타겟 ID (예: workfluence)", min_length=1)


class Summary(BaseModel):
    added: int = Field(description="추가된 동작 수")
    removed: int = Field(description="삭제된 동작 수")
    modified: int = Field(description="변경된 동작 수 (docOnly 포함)")
    docOnly: int = Field(description="변경 중 설명 문구만 바뀐 동작 수")
    documentChanges: int = Field(description="동작이 아닌 문서 수준 변경 수")


class DetectChanged(BaseModel):
    status: Literal["changed"]
    targetId: str
    changeSetId: str = Field(description="다음 단계(Manufacture 변경 반영)에 넘길 변경분 ID")
    fromVersion: str | None = Field(description="이전 명세 버전(sha256). 첫 실행이면 null")
    toVersion: str = Field(description="새 명세 버전(sha256)")
    summary: Summary
    needsReview: bool = Field(description="사람 확인이 필요한 변경이 있는지")
    reviewReasons: list[str]


class DetectUnchanged(BaseModel):
    status: Literal["unchanged"]
    targetId: str
    currentVersion: str = Field(description="현재 명세 버전(sha256)")


class OperationItem(BaseModel):
    method: str
    path: str
    operationId: str | None
    change: Literal["added", "removed", "modified"]
    before: dict[str, Any] | None = Field(None, description="옛 동작 조각 (removed·modified에만)")
    after: dict[str, Any] | None = Field(None, description="새 동작 조각 (added·modified에만)")
    changedFields: list[str] | None = Field(None, description="달라진 조각 키 (modified에만)")
    docOnly: bool | None = Field(None, description="설명 문구만 바뀌었는지 (modified에만)")


class DocumentChangeItem(BaseModel):
    section: str
    change: Literal["added", "removed", "modified"]


class ChangeSetBody(BaseModel):
    changeSetId: str
    targetId: str
    fromVersion: str | None
    toVersion: str
    createdAt: str
    needsReview: bool
    reviewReasons: list[str]
    summary: Summary
    operations: list[OperationItem]
    documentChanges: list[DocumentChangeItem]
    textDiff: str | None = Field(None, description="줄 단위 diff (includeTextDiff=true일 때만)")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = Field(None, description="오류의 세부 (예: 검증 요청의 tool별 거절 이유)")


# ── Phase 2 보강: 타겟 정보·실호출 검증 (Phase 2 설계 11.3·11.5절)


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targetId: str = Field(min_length=1)
    verificationId: str = Field(pattern=r"^vf-[A-Za-z0-9_-]{1,120}$",
                                description="부르는 쪽이 정한다. 같은 id로 다시 보내면 새로 실행하지 않는다")
    tools: list[dict[str, Any]] = Field(min_length=1, description="검증할 tool 정의 (a4w.tool/v1)")
    references: list[dict[str, Any]] = Field(default_factory=list,
                                             description="상위 tool이 참조하지만 이번 검증 대상이 아닌 단순 tool 정의")
    scenarios: dict[str, dict[str, Any]] = Field(default_factory=dict, description="tool 이름 → 검증 시나리오")


class VerificationAccepted(BaseModel):
    verificationId: str
    targetId: str
    status: Literal["running"]


class VerificationResultItem(BaseModel):
    tool: str
    revision: str
    result: Literal["passed", "failed", "unverified", "skipped"]
    calls: int
    elapsedMs: int
    reason: str | None = None
    error: dict[str, Any] | None = None


class VerificationBody(BaseModel):
    verificationId: str
    targetId: str
    status: Literal["running", "done", "failed"]
    startedAt: str
    finishedAt: str | None
    request: dict[str, Any] = Field(description="요청 요약 (tool 이름·revision, 시나리오 이름)")
    results: list[VerificationResultItem]
    sandbox: dict[str, Any] | None
    error: dict[str, Any] | None


class VerificationInfo(BaseModel):
    available: bool
    sandbox: str | None
    contextKeys: list[str] = Field(description="시나리오에서 쓸 수 있는 ${sandbox.*} 이름")


class ProfileBody(BaseModel):
    targetId: str
    tokenScopes: list[str] = Field(description="타겟 token의 scope. token 값은 내주지 않는다")
    verification: VerificationInfo


class ErrorBody(BaseModel):
    error: ErrorDetail


class Health(BaseModel):
    status: Literal["ok", "error"]
    specStore: Literal["ok", "error"]
