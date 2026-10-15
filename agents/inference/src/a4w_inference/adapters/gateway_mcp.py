"""ToolGateway 구현: McpAgentPool 위에서 agent별 MCP server를 부른다 (Phase 4 설계 10.2절)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..engine.models import ToolDef, ToolOutcome
from ..engine.ports import ToolGateway
from .mcp_pool import McpAgentPool


class McpToolGateway(ToolGateway):
    def __init__(self, pool: McpAgentPool):
        self._pool = pool

    async def definitions(self, agent_id: str) -> list[ToolDef]:
        return await self._pool.connection(agent_id).definitions()

    async def call(self, agent_id: str, mcp_name: str, arguments: Mapping[str, Any], *, expected_revision: str | None,
                   timeout_seconds: float) -> ToolOutcome:
        return await self._pool.connection(agent_id).call(mcp_name, arguments, expected_revision=expected_revision,
                                                          timeout_seconds=timeout_seconds)

    def generation(self, agent_id: str) -> int:
        return self._pool.connection(agent_id).generation

    async def refresh(self, agent_id: str) -> None:
        await self._pool.connection(agent_id).refresh()
