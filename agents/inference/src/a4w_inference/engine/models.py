"""engine이 주고받는 값 (Phase 4 설계 2·6·11절). 채널·HTTP·DB를 모른다."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class TurnRequest:
    agent_id: str
    text: str
    requester: str            # 감사 기록용 자기 신고 값. 모델에 보내지 않는다
    conversation_id: str | None = None
    allow_writes: bool = True             # 거짓이면 쓰기 tool을 모델에 싣지 않고 실행도 막는다 (Phase 5 FR-C32)
    channel_notes: str | None = None      # 채널이 시스템 프롬프트 끝에 덧붙이는 문구 (Phase 5 FR-C23)


@dataclass(frozen=True)
class Candidate:
    target_id: str
    name: str
    revision: str
    score: float


@dataclass(frozen=True)
class ToolDef:
    mcp_name: str
    description: str
    input_schema: dict[str, Any]
    target_id: str
    name: str
    revision: str
    risk: str
    group: str = ""


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    data: Any = None                       # 성공: MCP structuredContent
    error: dict[str, Any] | None = None    # 실패: {code, message, hint, details, retryable, …}
    request_id: str | None = None          # 타겟 요청 id (a4w/requestId)
    attempts: int = 1


@dataclass(frozen=True)
class ActionSummary:
    tool: str
    revision: str | None
    ok: bool
    error_code: str | None
    target_request_id: str | None
    write: bool
    args: dict[str, str]                   # 표시를 붙인 뒤 실제로 보낸 값의 요약(길이 제한)

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "revision": self.revision, "ok": self.ok, "errorCode": self.error_code,
                "targetRequestId": self.target_request_id, "write": self.write, "args": dict(self.args)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionSummary:
        return cls(tool=d["tool"], revision=d.get("revision"), ok=d["ok"], error_code=d.get("errorCode"),
                   target_request_id=d.get("targetRequestId"), write=d.get("write", False), args=dict(d.get("args") or {}))


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    steps: int = 0
    tool_calls: int = 0
    write_calls: int = 0
    last_input_tokens: int = 0      # 마지막 모델 호출의 실제 입력 크기(usage.prompt_tokens)
    last_input_chars: int = 0       # 그때 보낸 입력의 글자 수 (글자/토큰 비율 보정용)
    history_dropped_turns: int = 0
    trimmed_results: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Usage:
        names = {f.name for f in fields(cls)}
        return cls(**{k: int(v) for k, v in d.items() if k in names})


@dataclass(frozen=True)
class HistoryTurn:
    request_id: str
    text: str
    answer: str
    actions: tuple[ActionSummary, ...] = ()


@dataclass(frozen=True)
class TurnResult:
    answer: str | None
    actions: tuple[ActionSummary, ...]
    usage: Usage
    complete: bool
    stop_reason: str | None = None


class RunFailed(Exception):
    """수행이 실패로 끝났다. 이미 한 쓰기는 actions에 남는다."""

    def __init__(self, code: str, message: str, actions: tuple[ActionSummary, ...] = (), usage: Usage | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.actions = tuple(actions)
        self.usage = usage or Usage()


class McpUnavailable(Exception):
    """agent의 MCP server가 없거나 다시 뜨는 중이다."""


class SearchUnavailable(Exception):
    """tool 검색(AI Pro Plus)이 실패했다."""
