"""LlmClient interface (Phase 2 설계 10절, FR-251). 코어는 이것에만 의존한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

ErrorKind = Literal["auth", "timeout", "rate_limit", "output_format", "unavailable"]


@dataclass(frozen=True)
class LlmRequest:
    system: str
    user: str
    output_schema: dict[str, Any]
    model: str
    timeout_seconds: float
    max_output_tokens: int
    temperature: float | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"promptTokens": self.prompt_tokens, "completionTokens": self.completion_tokens,
                "reasoningTokens": self.reasoning_tokens}


@dataclass(frozen=True)
class LlmResult:
    value: dict[str, Any]       # 출력 스키마를 통과한 값
    model: str                  # 응답이 말한 실제 모델
    usage: LlmUsage
    attempts: int


class LlmError(Exception):
    def __init__(self, kind: ErrorKind, message: str, attempts: int = 1):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.attempts = attempts


class LlmClient(ABC):
    @abstractmethod
    def complete(self, request: LlmRequest) -> LlmResult:
        """구조화된 결과를 돌려주거나 LlmError(kind)를 낸다. 재시도는 구현이 한다."""
