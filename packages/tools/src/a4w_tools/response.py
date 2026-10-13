"""응답 가공: 필드 고르기와 크기 상한 (설계 15.4절)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_MISSING = object()


@dataclass(frozen=True)
class Shaped:
    data: Any
    truncated: bool = False
    original_count: int | None = None


def shape(data: Any, fields: Sequence[str] | None, max_chars: int) -> Shaped:
    if fields and isinstance(data, (dict, list)):
        picked = _MISSING
        for path in fields:
            got = _pick(data, _tokens(path))
            if got is not _MISSING:
                picked = got if picked is _MISSING else _merge(picked, got)
        data = {} if picked is _MISSING else picked
    return _limit(data, max_chars)


def size(data: Any) -> int:
    # 결과를 다시 직렬화하는 쪽(MCP 등)이 어떤 구분자를 쓰든 넘지 않게, 넉넉한 기본 구분자로 잰다
    return len(json.dumps(data, ensure_ascii=False))


def _tokens(path: str) -> list[str]:
    out: list[str] = []
    for part in path.split("."):
        if part.endswith("[]"):
            if part[:-2]:
                out.append(part[:-2])
            out.append("[]")
        else:
            out.append(part)
    return out


def _pick(value: Any, tokens: list[str]) -> Any:
    if not tokens:
        return value
    head, rest = tokens[0], tokens[1:]
    if head == "[]":
        if not isinstance(value, list):
            return _MISSING
        items = [_pick(v, rest) for v in value]
        return [{} if i is _MISSING else i for i in items]
    if not isinstance(value, dict) or head not in value:
        return _MISSING
    sub = _pick(value[head], rest)
    return _MISSING if sub is _MISSING else {head: sub}


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_merge(x, y) for x, y in zip(a, b)]
    return b


def _limit(data: Any, max_chars: int) -> Shaped:
    if size(data) <= max_chars:
        return Shaped(data)
    holder, key = _largest_list(data)
    if holder is not None:
        items = holder if key is None else holder[key]
        original = len(items)
        trimmed = list(items)
        while trimmed:
            trimmed.pop()
            candidate = trimmed if key is None else {**holder, key: trimmed}
            if size(candidate) <= max_chars:
                return Shaped(candidate, truncated=True, original_count=original)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return Shaped(text[:max_chars], truncated=True)


def _largest_list(data: Any) -> tuple[Any, str | None]:
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        lists = [(size(v), k) for k, v in data.items() if isinstance(v, list) and v]
        if lists:
            return data, max(lists)[1]
    return None, None
