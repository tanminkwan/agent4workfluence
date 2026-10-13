"""tool 실행 결과와 오류. 실행 결과는 예외가 아니라 값으로 돌려준다."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 오류 코드 (설계 15.1절)
INPUT_INVALID = "INPUT_INVALID"
TARGET_ERROR = "TARGET_ERROR"
RATE_LIMITED = "RATE_LIMITED"
TIMEOUT = "TIMEOUT"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"
STEP_FAILED = "STEP_FAILED"
UNSUPPORTED = "UNSUPPORTED"
CONTEXT_MISSING = "CONTEXT_MISSING"


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    target_code: str | None = None
    request_id: str | None = None
    http_status: int | None = None
    retryable: bool = False
    hint: str | None = None
    step: str | None = None
    details: Any = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {
            "code": d["code"],
            "message": d["message"],
            "targetCode": d["target_code"],
            "requestId": d["request_id"],
            "httpStatus": d["http_status"],
            "retryable": d["retryable"],
            "hint": d["hint"],
            "step": d["step"],
            "details": d["details"],
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    truncated: bool = False
    error: ToolError | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(cls, error: ToolError, data: Any = None, **meta: Any) -> "ToolResult":
        return cls(ok=False, data=data, error=error, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "truncated": self.truncated,
            "error": self.error.to_dict() if self.error else None,
            "meta": self.meta,
        }


class ToolFailure(Exception):
    """실행 도중 결과를 실패로 끝낼 때 내부에서만 쓴다."""

    def __init__(self, error: ToolError):
        super().__init__(error.message)
        self.error = error
