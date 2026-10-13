"""시나리오 값 채우기: `${sandbox.*}`, `${run.id}`, `${saved.*}`, `${setup.<n>.*}` (Phase 2 설계 11.7절)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PLACEHOLDER = re.compile(r"\$\{([a-z]+)\.([A-Za-z0-9_.]+)\}")
SOURCES = frozenset({"sandbox", "run", "saved", "setup"})


class ScenarioError(ValueError):
    pass


def placeholders(value: Any) -> list[tuple[str, str]]:
    """값 안의 모든 자리표시 (출처, 경로)."""
    if isinstance(value, str):
        return PLACEHOLDER.findall(value)
    if isinstance(value, Mapping):
        return [p for v in value.values() for p in placeholders(v)]
    if isinstance(value, list):
        return [p for v in value for p in placeholders(v)]
    return []


def fill(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value)
        if whole:
            return _lookup(variables, *whole.groups())
        return PLACEHOLDER.sub(lambda m: str(_lookup(variables, *m.groups())), value)
    if isinstance(value, Mapping):
        return {k: fill(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    return value


def _lookup(variables: Mapping[str, Any], source: str, path: str) -> Any:
    node: Any = variables.get(source)
    if node is None:
        raise ScenarioError(f"모르는 출처: {source}")
    for part in path.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            raise ScenarioError(f"값이 없다: ${{{source}.{path}}}")
    return node
