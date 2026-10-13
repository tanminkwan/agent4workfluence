"""기계 변환 interface와 골격 (Phase 2 설계 7.1절). 골격의 HTTP 매핑과 입력 칸은 코드만 만든다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InputField:
    name: str                 # tool 입력 칸 이름
    location: str             # path | query | header | body
    wire: str | None          # path·query·header의 이름
    pointer: str | None       # body의 JSON Pointer
    schema: dict[str, Any]    # JSON Schema (명세 설명 포함)
    required: bool
    description: str          # 명세의 설명 (없으면 "")

    def binding(self) -> dict[str, Any]:
        if self.location == "body":
            return {"input": self.name, "in": "body", "pointer": self.pointer}
        return {"input": self.name, "in": self.location, "name": self.wire}

    def fixed(self, value: Any) -> dict[str, Any]:
        """고정값 항목. 값이 {"$context": 키}면 값 대신 맥락 키를 둔다 (실행하는 쪽이 채운다)."""
        place = {"in": "body", "pointer": self.pointer} if self.location == "body" else {"in": self.location, "name": self.wire}
        if isinstance(value, dict) and set(value) == {"$context"}:
            return {**place, "context": value["$context"]}
        return {**place, "value": value}

    @property
    def machine_hidden(self) -> bool:
        """기계 기본 숨김: 명세 기본값이 있는 선택 칸 (설계 6.3.1). 글 쪽이 일부러 드러낼 때만 보인다."""
        return not self.required and self.location != "path" and "default" in self.schema


@dataclass(frozen=True)
class Skeleton:
    operation_id: str | None
    method: str
    path: str
    inputs: tuple[InputField, ...]
    http: dict[str, Any]                  # method, path, [contentType], bindings, fixed, success
    response_paths: tuple[str, ...]       # 응답 스키마의 필드 경로 (response.fields 검사용)
    scopes: tuple[str, ...]
    summary: str = ""
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def input(self, name: str) -> InputField | None:
        return next((i for i in self.inputs if i.name == name), None)


class SpecTransformer(ABC):
    @abstractmethod
    def skeleton(self, fragment: Mapping[str, Any], *, fixed: Mapping[str, Mapping[str, Any]]) -> Skeleton:
        """같은 조각과 고정값이면 늘 같은 골격 (결정성)."""
