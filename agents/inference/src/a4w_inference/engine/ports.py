"""engine이 의존하는 interface (DIP). 구현은 adapters·storage에 있다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from .models import Candidate, HistoryTurn, ToolDef, ToolOutcome, TurnRequest, TurnResult


class ToolSearch(ABC):
    @abstractmethod
    async def search(self, agent_id: str, query: str) -> list[Candidate]:
        """질의로 그 agent의 타겟 색인을 검색한다. 실패는 SearchUnavailable."""


class ToolGateway(ABC):
    @abstractmethod
    async def definitions(self, agent_id: str) -> list[ToolDef]: ...

    @abstractmethod
    async def call(self, agent_id: str, mcp_name: str, arguments: Mapping[str, Any], *, expected_revision: str | None,
                   timeout_seconds: float) -> ToolOutcome:
        """tool을 실행한다. 없는 tool·revision 불일치는 오류 결과(code), server가 없으면 McpUnavailable."""

    @abstractmethod
    def generation(self, agent_id: str) -> int:
        """tool 목록이 바뀔 때마다 늘어나는 값."""

    @abstractmethod
    async def refresh(self, agent_id: str) -> None:
        """tool 목록을 새로 받는다."""


class ConversationStore(ABC):
    @abstractmethod
    async def history(self, agent_id: str, conversation_id: str, *, limit: int) -> list[HistoryTurn]:
        """같은 대화의 앞선 차례(오래된 순)."""

    @abstractmethod
    async def last_chars_per_token(self, agent_id: str) -> float | None:
        """마지막 실측 글자/토큰 비율. 없으면 None."""


class TurnEngine(ABC):
    """채널·runner가 아는 수행 로직의 모양."""

    @abstractmethod
    async def run(self, request: TurnRequest) -> TurnResult: ...
