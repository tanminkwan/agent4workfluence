"""정의 검사 (Phase 2 설계 8절). seed·LLM 출력 모두 같은 검사를 거친다. 오류 코드는 LLM 재요청에 그대로 붙인다."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from a4w_core.errors import ConfigError
from a4w_core.openapi.canonical import canonical_json
from a4w_tools.validate import validate_tool

from .assembly import (
    NO_DEFAULT, assemble, authoring_to_raw, extract_authoring, invented_inputs, missing_inputs, parse_authoring,
    visible_inputs,
)
from .transform.base import Skeleton

NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
# 예시 질의에 들어가면 안 되는 id 같은 값: UUID, 긴 16진, 6자리 이상 숫자
# (한글은 단어 문자라 \b를 쓰지 않고 앞뒤 글자로 자른다)
ID_LIKE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                     r"|(?<![0-9a-f])[0-9a-f]{12,}(?![0-9a-f])|(?<!\d)\d{6,}(?!\d)", re.IGNORECASE)
MIN_EXAMPLES = 3

# LLM 출력 스키마 (create·update). 개수 하한은 TEXT_LIMIT으로 따로 본다
AUTHORING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "title", "description", "inputs", "index"],
    "properties": {
        "name": {"type": "string"},
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "inputs": {"type": "object", "additionalProperties": {
            "type": "object", "additionalProperties": False, "required": ["description"],
            "properties": {"description": {"type": "string"}, "hide": {"type": "boolean"}, "default": {}}}},
        "responseFields": {"type": "array", "items": {"type": "string"}},
        "index": {"type": "object", "additionalProperties": False, "required": ["summary", "examples"],
                  "properties": {"summary": {"type": "string", "minLength": 1},
                                 "examples": {"type": "array", "items": {"type": "string", "minLength": 1}}}},
    },
}


@dataclass(frozen=True)
class Problem:
    code: str
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class TextLimits:
    description: int
    summary: int
    example: int

    @classmethod
    def parse(cls, text: str) -> "TextLimits":
        try:
            values = {k.strip(): int(v) for k, v in (p.split("=", 1) for p in text.split(",") if p.strip())}
            return cls(description=values["description"], summary=values["summary"], example=values["example"])
        except (KeyError, ValueError):
            raise ConfigError("TOOL_TEXT_LIMITS는 description=N,summary=N,example=N 꼴이어야 한다") from None


class DefinitionChecker:
    def __init__(self, limits: TextLimits, *, max_visible_inputs: int):
        self._limits = limits
        self._max_visible = max_visible_inputs
        self._validator = Draft202012Validator(AUTHORING_SCHEMA)

    def check_authoring(self, skeleton: Skeleton, raw: Any, *, taken_names: Collection[str]) -> list[Problem]:
        shape = [Problem("SCHEMA_INVALID", "/".join(map(str, e.absolute_path)) or "(root)", e.message)
                 for e in self._validator.iter_errors(raw)]
        if shape:
            return shape
        out: list[Problem] = []
        known = {i.name: i for i in skeleton.inputs}
        given = raw["inputs"]
        for name in known:
            if name not in given:
                out.append(Problem("INPUT_MISSING", f"inputs.{name}", "명세의 입력 칸이 빠졌다"))
        for name in given:
            if name not in known:
                out.append(Problem("INPUT_INVENTED", f"inputs.{name}", "명세에 없는 입력 칸이다"))
        for name, item in given.items():
            inp = known.get(name)
            if inp is None:
                continue
            has_default = "default" in item
            if item.get("hide") and not has_default and (inp.required or inp.location == "path"):
                out.append(Problem("HIDDEN_REQUIRED", f"inputs.{name}", "필수 칸·경로 칸은 기본값 없이 숨길 수 없다"))
            if has_default:
                errors = list(Draft202012Validator(inp.schema).iter_errors(item["default"]))
                if errors:
                    out.append(Problem("DEFAULT_INVALID", f"inputs.{name}.default", errors[0].message))
        out.extend(self._common(skeleton, raw, taken_names))
        return out

    def check_seed(self, skeleton: Skeleton, seed: Mapping[str, Any], *, taken_names: Collection[str]) -> list[Problem]:
        out = [Problem("SCHEMA_INVALID", "(seed)", e) for e in validate_tool(seed)]
        if out:
            return out
        for name in missing_inputs(seed, skeleton):
            out.append(Problem("INPUT_MISSING", f"inputs.{name}", "명세의 입력 칸이 seed에 없다(보이지도 고정되지도 않음)"))
        for name in invented_inputs(seed, skeleton):
            out.append(Problem("INPUT_INVENTED", f"inputs.{name}", "명세에 없는 입력 칸이다"))
        authoring = extract_authoring(seed, skeleton)
        for name, ia in authoring.inputs.items():
            if ia.default is not NO_DEFAULT:
                errors = list(Draft202012Validator(skeleton.input(name).schema).iter_errors(ia.default))
                if errors:
                    out.append(Problem("DEFAULT_INVALID", f"inputs.{name}.default", errors[0].message))
        out.extend(self._common(skeleton, authoring_to_raw(authoring), taken_names))
        rebuilt = assemble(skeleton, authoring, group=seed["group"], risk=seed["risk"], origin=seed["origin"],
                           source=seed.get("source", {}))
        if canonical_json(rebuilt["http"]) != canonical_json(seed["http"]):
            out.append(Problem("MAPPING_CHANGED", "http", "HTTP 매핑이 기계 변환 결과와 다르다"))
        return out

    def _common(self, skeleton: Skeleton, raw: Mapping[str, Any], taken_names: Collection[str]) -> list[Problem]:
        out: list[Problem] = []
        visible = visible_inputs(skeleton, parse_authoring(raw, max_chars=0))
        if len(visible) > self._max_visible:
            out.append(Problem("TOO_MANY_INPUTS", "inputs",
                               f"보이는 칸이 {len(visible)}개다({', '.join(visible)}). {self._max_visible}개 이하로 줄여라: "
                               "기본값을 주고 숨기거나, 서버 기본값을 쓰도록 숨겨라"))
        name = raw["name"]
        if not NAME.match(name):
            out.append(Problem("NAME_FORMAT", "name", "이름은 ^[a-z][a-z0-9_]{2,63}$ 꼴이어야 한다"))
        if name in taken_names:
            out.append(Problem("NAME_TAKEN", "name", f"다른 동작의 tool 이름이다: {name}"))
        for f in raw.get("responseFields") or []:
            if f not in skeleton.response_paths:
                out.append(Problem("RESPONSE_FIELD_UNKNOWN", "responseFields", f"응답 스키마에 없는 경로: {f}"))
        examples = raw["index"]["examples"]
        for i, ex in enumerate(examples):
            if ID_LIKE.search(ex):
                out.append(Problem("EXAMPLE_HAS_ID", f"index.examples[{i}]", "예시 질의에 id 같은 값을 쓰지 않는다"))
            if len(ex) > self._limits.example:
                out.append(Problem("TEXT_LIMIT", f"index.examples[{i}]", f"{self._limits.example}자를 넘었다"))
        if len(examples) < MIN_EXAMPLES:
            out.append(Problem("TEXT_LIMIT", "index.examples", f"예시 질의는 {MIN_EXAMPLES}개 이상이어야 한다"))
        if len(raw["description"]) > self._limits.description:
            out.append(Problem("TEXT_LIMIT", "description", f"{self._limits.description}자를 넘었다"))
        if len(raw["index"]["summary"]) > self._limits.summary:
            out.append(Problem("TEXT_LIMIT", "index.summary", f"{self._limits.summary}자를 넘었다"))
        if len(raw["title"]) > self._limits.summary:
            out.append(Problem("TEXT_LIMIT", "title", f"{self._limits.summary}자를 넘었다"))
        return out


def build_from_raw(skeleton: Skeleton, raw: Mapping[str, Any], *, max_chars: int, **kw) -> dict[str, Any]:
    return assemble(skeleton, parse_authoring(raw, max_chars=max_chars), **kw)
