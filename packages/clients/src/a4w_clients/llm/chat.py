"""ChatLlmClient interface (Phase 4 설계 9절, FR-I51). tool calling이 있는 대화용이고, 구조화 출력용 LlmClient와 분리했다(ISP).

사용자 확정 2026-10-15: Manufacture는 chat을 모른다. 구현체(OpenAiCompatibleClient)가 두 interface를 모두 구현한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from .base import LlmUsage


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str      # 모델이 낸 JSON 문자열 그대로. 올바른 JSON인지는 쓰는 쪽이 판단한다


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...]
    model: str
    timeout_seconds: float
    max_output_tokens: int
    temperature: float | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ChatResult:
    message: ChatMessage      # assistant: content 또는 tool_calls
    finish_reason: str        # stop | tool_calls | length
    usage: LlmUsage
    model: str
    attempts: int


class ChatLlmClient(ABC):
    @abstractmethod
    def chat(self, request: ChatRequest) -> ChatResult:
        """대화 한 차례. 실패는 LlmError(kind)다. 재시도(시간 초과·429·5xx)는 구현이 한다."""
