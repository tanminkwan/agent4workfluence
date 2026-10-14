"""tool 실행 경로 (Phase 3 설계 8절). 구현은 agent token으로 직접 부르는 것 하나다.

Detection 경유 실행(agent가 아닌 client가 공용 계정으로 실행)은 이 interface만 있고 구현이 없다(FR-T51).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from a4w_tools.errors import ToolResult
from a4w_tools.executor import ToolExecutor

from .snapshot import ServedTool


class ToolRunner(ABC):
    @abstractmethod
    def run(self, tool: ServedTool, args: Mapping[str, Any], toolset: Mapping[str, Mapping[str, Any]]) -> ToolResult:
        """동기 호출이다. server가 worker thread에서 부른다."""


class DirectToolRunner(ToolRunner):
    def __init__(self, *, executors: Mapping[str, ToolExecutor], contexts: Mapping[str, Mapping[str, str]]):
        self._executors = dict(executors)
        self._contexts = {t: dict(c) for t, c in contexts.items()}

    def run(self, tool: ServedTool, args: Mapping[str, Any], toolset: Mapping[str, Mapping[str, Any]]) -> ToolResult:
        return self._executors[tool.target_id].execute(
            tool.definition, args, toolset=toolset, context=self._contexts[tool.target_id])
