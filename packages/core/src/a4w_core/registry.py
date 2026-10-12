"""이름 → 구현 factory 등록부. 새 구현은 등록만 하면 된다 (OCP)."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from .errors import ConfigError

KINDS = ("spec_source", "spec_parser", "spec_normalizer", "review_policy")
ENTRY_POINT_GROUP = "a4w.targets"

Factory = Callable[[Any], Any]


class Registry:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Factory]] = {k: {} for k in KINDS}

    def register(self, kind: str, name: str, factory: Factory) -> None:
        if kind not in self._items:
            raise ValueError(f"알 수 없는 종류: {kind}")
        if name in self._items[kind]:
            raise ValueError(f"이미 등록됨: {kind}/{name}")
        self._items[kind][name] = factory

    def create(self, kind: str, name: str, config: Any) -> Any:
        try:
            factory = self._items[kind][name]
        except KeyError:
            raise ConfigError(f"등록되지 않은 {kind}: {name}") from None
        return factory(config)

    def names(self, kind: str) -> list[str]:
        return sorted(self._items[kind])


def load_target_plugins(registry: Registry) -> list[str]:
    """설치된 타겟 패키지(entry point `a4w.targets`)를 모두 등록한다."""
    loaded = []
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        ep.load()(registry)
        loaded.append(ep.name)
    return loaded
