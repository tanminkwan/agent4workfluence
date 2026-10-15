"""agent마다 engine을 두고 agent id로 고른다 (Phase 4 설계 4.4: agent마다 프롬프트·표시 설정이 다를 수 있다)."""

from __future__ import annotations

from collections.abc import Mapping

from .models import TurnRequest, TurnResult
from .ports import TurnEngine


class EngineRouter(TurnEngine):
    def __init__(self, engines: Mapping[str, TurnEngine]):
        self._engines = dict(engines)

    async def run(self, request: TurnRequest) -> TurnResult:
        return await self._engines[request.agent_id].run(request)
