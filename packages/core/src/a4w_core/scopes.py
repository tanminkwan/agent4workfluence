"""설정으로 받는 token scope (token에서 scope를 읽을 수 없는 타겟용)."""

from __future__ import annotations

from .config import TargetConfig
from .interfaces import ScopeProvider


class ConfigScopeProvider(ScopeProvider):
    def __init__(self, scopes: frozenset[str]):
        self._scopes = scopes

    @classmethod
    def from_config(cls, cfg: TargetConfig) -> "ConfigScopeProvider":
        return cls(frozenset(cfg.require_list("TARGET_TOKEN_SCOPES")))

    def scopes(self, token: str | None) -> frozenset[str]:
        return self._scopes
