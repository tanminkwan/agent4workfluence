"""RequestStore(동기 SQLite)를 engine의 ConversationStore(async)로 맞춘다."""

from __future__ import annotations

import anyio

from ..engine.models import HistoryTurn
from ..engine.ports import ConversationStore
from ..records import RequestStore


class ThreadedConversationStore(ConversationStore):
    def __init__(self, store: RequestStore):
        self._store = store

    async def history(self, agent_id: str, conversation_id: str, *, limit: int) -> list[HistoryTurn]:
        return await anyio.to_thread.run_sync(lambda: self._store.history(agent_id, conversation_id, limit=limit))

    async def last_chars_per_token(self, agent_id: str) -> float | None:
        return await anyio.to_thread.run_sync(self._store.last_chars_per_token, agent_id)
