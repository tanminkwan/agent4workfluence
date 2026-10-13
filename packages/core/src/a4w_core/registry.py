"""이름 → 구현 factory 등록부. 새 구현은 등록만 하면 된다 (OCP).

종류(kind)는 고정 목록이 아니다. 처음 등록할 때 생긴다. 서비스마다 다른 종류
(LLM, RAG, 검증 환경 등)를 core를 고치지 않고 더하기 위해서다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from .errors import ConfigError

ENTRY_POINT_GROUP = "a4w.targets"
_KIND = re.compile(r"^[a-z][a-z0-9_]*$")

Factory = Callable[[Any], Any]


class Registry:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Factory]] = {}

    def register(self, kind: str, name: str, factory: Factory) -> None:
        if not _KIND.match(kind):
            raise ValueError(f"종류 이름 형식이 아니다: {kind!r}")
        names = self._items.setdefault(kind, {})
        if name in names:
            raise ValueError(f"이미 등록됨: {kind}/{name}")
        names[name] = factory

    def create(self, kind: str, name: str, config: Any) -> Any:
        try:
            factory = self._items[kind][name]
        except KeyError:
            raise ConfigError(f"등록되지 않은 {kind}: {name}") from None
        return factory(config)

    def names(self, kind: str) -> list[str]:
        return sorted(self._items.get(kind, {}))


def load_target_plugins(registry: Registry) -> list[str]:
    """설치된 타겟 패키지(entry point `a4w.targets`)를 모두 등록한다."""
    loaded = []
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        ep.load()(registry)
        loaded.append(ep.name)
    return loaded
