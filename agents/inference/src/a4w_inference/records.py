"""요청 기록과 그 저장소 interface (Phase 4 설계 11절). runner는 이 interface에만 의존한다(DIP)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from .engine.models import ActionSummary, HistoryTurn, TurnResult, Usage


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    agent_id: str
    requester: str
    conversation_id: str | None
    idempotency_key: str | None
    text: str
    status: str                      # running | done | failed
    complete: bool | None
    stop_reason: str | None
    answer: str | None
    error_code: str | None
    error_message: str | None
    actions: tuple[ActionSummary, ...]
    usage: Usage
    created_at: str
    finished_at: str | None
    elapsed_ms: int | None


class RequestStore(ABC):
    """요청 기록 + 대화 이력 + 글자/토큰 비율 보정값."""

    @abstractmethod
    def create_running(self, *, request_id: str, agent_id: str, requester: str, text: str, conversation_id: str | None,
                       idempotency_key: str | None, created_at: str) -> None: ...

    @abstractmethod
    def get(self, request_id: str) -> RequestRecord | None: ...

    @abstractmethod
    def finish_done(self, request_id: str, result: TurnResult, *, finished_at: str, elapsed_ms: int) -> None: ...

    @abstractmethod
    def finish_failed(self, request_id: str, *, code: str, message: str, actions: Sequence[ActionSummary], usage: Usage,
                      finished_at: str, elapsed_ms: int) -> None: ...

    @abstractmethod
    def find_by_key(self, agent_id: str, key: str) -> RequestRecord | None: ...

    @abstractmethod
    def running_in_conversation(self, agent_id: str, conversation_id: str) -> bool: ...

    @abstractmethod
    def history(self, agent_id: str, conversation_id: str, *, limit: int) -> list[HistoryTurn]: ...

    @abstractmethod
    def last_chars_per_token(self, agent_id: str) -> float | None: ...

    @abstractmethod
    def mark_interrupted(self, *, finished_at: str) -> int: ...

    @abstractmethod
    def purge(self, *, before: str) -> int: ...
