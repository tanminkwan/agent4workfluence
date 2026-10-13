"""자리만 둔 provider (FR-252). Anthropic API·Claude Code CLI는 보류했다(사용자 결정, 2026-10-13)."""

from __future__ import annotations

from a4w_core.errors import ConfigError
from a4w_core.registry import Registry

from . import openai_compatible


def _unsupported(name: str):
    def factory(cfg):
        raise ConfigError(f"LLM_PROVIDER={name}는 자리만 있다 (구현 안 함)")
    return factory


def register(registry: Registry) -> None:
    """factory는 (env, secrets) 묶음을 받는다."""
    registry.register("llm_client", "openai_compatible", lambda ctx: openai_compatible.from_settings(*ctx))
    registry.register("llm_client", "anthropic", _unsupported("anthropic"))
    registry.register("llm_client", "claude_cli", _unsupported("claude_cli"))
