"""'AI agent 작성' 표시를 쓰기 입력에 붙인다 (Phase 4 설계 7.2, FR-I24). 표시 칸이 설정에 없는 쓰기 tool은 실행하지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a4w_core.errors import ConfigError


class MarkNotConfigured(Exception):
    """이 쓰기 tool의 표시 칸이 설정에 없다."""


class MarkNotApplicable(Exception):
    """표시를 붙일 칸의 값이 문자열이 아니다."""


class AuthorshipMarker:
    def __init__(self, marks: Mapping[str, Mapping[str, tuple[str, ...]]], suffix: str):
        self._marks = {t: dict(m) for t, m in marks.items()}
        self._suffix = suffix

    @staticmethod
    def decode_suffix(raw: str) -> str:
        return raw.replace("\\n", "\n")

    @staticmethod
    def parse_marks(text: str) -> dict[str, tuple[str, ...]]:
        """`tool:칸+칸,tool:-` → {tool: (칸,…)}. `-`는 글을 쓰는 칸이 없어 표시가 필요 없다는 명시다."""
        out: dict[str, tuple[str, ...]] = {}
        items = [i.strip() for i in text.split(",") if i.strip()]
        if not items:
            raise ConfigError("AGENT_WRITE_MARKS가 비었다")
        for item in items:
            tool, sep, fields = item.partition(":")
            tool, fields = tool.strip(), fields.strip()
            if not sep or not tool or not fields:
                raise ConfigError(f"AGENT_WRITE_MARKS 항목은 tool:칸 모양이다: {item!r}")
            if tool in out:
                raise ConfigError(f"AGENT_WRITE_MARKS에 tool이 두 번 있다: {tool}")
            if fields == "-":
                out[tool] = ()
                continue
            names = tuple(f.strip() for f in fields.split("+"))
            if any(not n or n == "-" for n in names):
                raise ConfigError(f"AGENT_WRITE_MARKS의 칸 이름이 비었다: {item!r}")
            out[tool] = names
        return out

    def apply(self, target_id: str, tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
        fields = self._marks.get(target_id, {}).get(tool)
        if fields is None:
            raise MarkNotConfigured(f"{target_id}/{tool}")
        out = dict(args)
        for name in fields:
            if name not in out or out[name] is None:
                continue
            value = out[name]
            if not isinstance(value, str):
                raise MarkNotApplicable(name)
            if not value.endswith(self._suffix):
                out[name] = value + self._suffix
        return out
