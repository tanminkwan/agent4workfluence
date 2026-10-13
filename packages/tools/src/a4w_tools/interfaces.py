"""타겟마다 다른 것(오류 해석)과 사람이 쓴 handler의 자리."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from .errors import TARGET_ERROR, ToolError

MESSAGE_MAX = 300


class ErrorInterpreter(ABC):
    """타겟의 실패 응답을 ToolError로 바꾼다. 오류 모양과 코드별 안내문은 타겟 어댑터가 안다."""

    @abstractmethod
    def interpret(self, status: int, headers: Mapping[str, str], body: bytes) -> ToolError: ...


class GenericErrorInterpreter(ErrorInterpreter):
    """`{"error": {"code", "message", "requestId"}}` 모양이면 꺼내고, 아니면 본문 앞부분만 전한다."""

    def interpret(self, status: int, headers: Mapping[str, str], body: bytes) -> ToolError:
        code, message, request_id = _parse(body)
        return ToolError(
            TARGET_ERROR,
            message or f"타겟이 {status}로 답했다",
            target_code=code,
            request_id=request_id,
            http_status=status,
            retryable=status >= 500 or status == 429,
        )


def _parse(body: bytes) -> tuple[str | None, str | None, str | None]:
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        text = body.decode("utf-8", errors="replace").strip()
        return None, text[:MESSAGE_MAX] or None, None
    err = doc.get("error") if isinstance(doc, dict) else None
    if isinstance(err, dict):
        msg = err.get("message")
        return err.get("code"), (str(msg)[:MESSAGE_MAX] if msg else None), err.get("requestId")
    return None, json.dumps(doc, ensure_ascii=False)[:MESSAGE_MAX], None


class Handler(ABC):
    """선언형으로 표현할 수 없는 tool을 사람이 코드로 쓴 것 (FR-224). Workfluence에는 없다."""

    @abstractmethod
    def run(self, args: Mapping[str, Any], executor: Any) -> Any: ...
